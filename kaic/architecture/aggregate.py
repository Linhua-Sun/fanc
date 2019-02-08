from genomic_regions import GenomicRegion, Bedpe
from collections import defaultdict
import numpy as np
from ..tools.general import RareUpdateProgressBar
from ..general import FileGroup
from scipy.misc import imresize
import tables

import logging

logger = logging.getLogger(__name__)


class AggregateMatrix(FileGroup):

    _classid = 'AGGREGATEMATRIX'

    def __init__(self, file_name=None, mode='r', tmpdir=None,
                 x=None, y=None):
        FileGroup.__init__(self, 'aggregate', file_name=file_name, mode=mode, tmpdir=tmpdir)

        self.x = x
        self.y = y

    def matrix(self, m=None):
        if m is not None:
            try:
                self.file.remove_node(self._group, 'aggregate_matrix')
            except tables.NoSuchNodeError:
                pass
            am = self.file.create_carray(self._group, 'aggregate_matrix', tables.Float32Atom(), m.shape)
            am[:] = m

        return self._group.aggregate_matrix[:]

    def region_pairs(self, pairs=None):
        if pairs is not None:
            try:
                self.file.remove_node(self._group, 'region_pairs')
            except tables.NoSuchNodeError:
                pass

            pairs_table = self.file.create_table(self._group, 'region_pairs',
                                                 description={
                                                     'chromosome1': tables.StringCol(50, pos=0),
                                                     'start1': tables.Int32Col(pos=1),
                                                     'end1': tables.Int32Col(pos=2),
                                                     'strand1': tables.Int32Col(pos=3),
                                                     'chromosome2': tables.StringCol(50, pos=4),
                                                     'start2': tables.Int32Col(pos=5),
                                                     'end2': tables.Int32Col(pos=6),
                                                     'strand2': tables.Int32Col(pos=7)
                                                 })

            row = pairs_table.row
            for r1, r2 in pairs:
                row['chromosome1'] = r1.chromosome
                row['start1'] = r1.start
                row['end1'] = r1.end
                row['strand1'] = r1.strand if r1.strand is not None else 0

                row['chromosome2'] = r2.chromosome
                row['start2'] = r2.start
                row['end2'] = r2.end
                row['strand2'] = r2.strand if r2.strand is not None else 0

                row.append()

            pairs_table.flush()

        pairs = []
        pairs_table = self.file.get_node(self._group, 'region_pairs')
        for row in pairs_table.iterrows():
            r1 = GenomicRegion(chromosome=row['chromosome1'], start=row['start1'],
                               end=row['end1'], strand=row['strand1'])
            r2 = GenomicRegion(chromosome=row['chromosome2'], start=row['start2'],
                               end=row['end2'], strand=row['strand2'])
            pairs.append((r1, r2))
        return pairs

    def components(self, components=None):
        if components is not None:
            try:
                self.file.remove_node(self._group, 'components', recursive=True)
            except tables.NoSuchNodeError:
                pass

            component_group = self.file.create_group(self._group, 'components')

            for i, m in enumerate(components):
                cm = self.file.create_carray(component_group, 'component_{}',
                                             tables.Float32Atom(), m.shape)
                cm[:] = m

                if hasattr(m, 'mask'):
                    mm = self.file.create_carray(component_group, 'mask_{}',
                                                 tables.BoolAtom(), m.shape)
                    mm[:] = m.mask

        max_ix = 0
        masks = dict()
        components = dict()
        component_group = self.file.get_node(self._group, 'components')
        for node in self.file.iter_nodes(component_group):
            if node.name.startswith('mask_'):
                ix = int(node.name[5:])
                masks[ix] = node[:]
                max_ix = max(ix, max_ix)
            elif node.name.startswith('component_'):
                ix = int(node.name[10:])
                components[ix] = node[:]
                max_ix = max(ix, max_ix)

        sorted_components = []
        for ix in range(max_ix):
            component = components[ix]
            if ix in masks:
                mask = masks[ix]
            else:
                mask = None

            sorted_components.append(np.ma.masked_array(component, mask=mask))
        return sorted_components

    @classmethod
    def from_center(cls, matrix, regions, window=200000,
                    rescale=False, scaling_exponent=-0.25,
                    keep_components=True,
                    file_name=None, tmpdir=None,
                    **kwargs):
        kwargs.setdefault('oe', True)
        kwargs.setdefault('keep_invalid', False)
        kwargs.setdefault('log', True)

        region_pairs = []
        for region in regions.regions:
            new_start = int(region.center - int(window / 2))
            new_end = int(region.center + int(window / 2))
            new_region = GenomicRegion(chromosome=region.chromosome, start=new_start, end=new_end,
                                       strand=region.strand)
            region_pairs.append((new_region, new_region))

        component_regions = []
        component_matrices = []
        counter_matrix = None
        matrix_sum = None
        for (r1, r2), m in extract_submatrices(matrix, region_pairs, **kwargs):
            if counter_matrix is None:
                shape = m.shape
                counter_matrix = np.zeros(shape)
                matrix_sum = np.zeros(shape)

            if hasattr(m, 'mask'):
                inverted_mask = ~m.mask
                counter_matrix += inverted_mask.astype('int')
            else:
                counter_matrix += np.ones(counter_matrix.shape)

            matrix_sum += m

            component_regions.append((r1, r2))
            if keep_components:
                component_matrices.append(m)

        am = matrix_sum / counter_matrix

        if rescale:
            am = _rescale_oe_matrix(am, matrix.bin_size, scaling_exponent=scaling_exponent)

        aggregate_object = cls(file_name=file_name, mode='w', tmpdir=tmpdir)
        aggregate_object.matrix(am)
        aggregate_object.region_pairs(component_regions)
        aggregate_object.components(component_matrices)

        return aggregate_object

    @classmethod
    def from_regions(cls, hic, tad_regions, pixels=90,
                     rescale=False, scaling_exponent=-0.25,
                     interpolation='nearest', keep_mask=True,
                     absolute_extension=0, relative_extension=1.0,
                     keep_components=True,
                     file_name=None, tmpdir=None,
                     **kwargs):
        kwargs.setdefault('oe', True)
        kwargs.setdefault('keep_invalid', False)
        kwargs.setdefault('log', True)

        component_regions = []
        component_matrices = []
        shape = (pixels, pixels)
        counter_matrix = np.zeros(shape)
        matrix_sum = np.zeros(shape)
        for (r1, r2), m in _tad_matrix_iterator(hic, tad_regions,
                                                absolute_extension=absolute_extension,
                                                relative_extension=relative_extension,
                                                **kwargs):
            ms = imresize(m, shape, interp=interpolation, mode='F')

            if keep_mask and hasattr(ms, 'mask'):
                mask = imresize(m.mask, shape, interp='nearest').astype('bool')
                ms = np.ma.masked_where(mask, ms)
                inverted_mask = ~mask
                counter_matrix += inverted_mask.astype('int')
            else:
                counter_matrix += np.ones(shape)

            matrix_sum += ms

            component_regions.append((r1, r2))
            if keep_components:
                component_matrices.append(m)

        am = matrix_sum/counter_matrix

        if rescale:
            am = _rescale_oe_matrix(am, hic.bin_size, scaling_exponent=scaling_exponent)

        aggregate_object = cls(file_name=file_name, mode='w', tmpdir=tmpdir)
        aggregate_object.matrix(am)
        aggregate_object.region_pairs(component_regions)
        aggregate_object.components(component_matrices)

        return aggregate_object

    @classmethod
    def from_center_pairs(cls, hic, pair_regions, window=None, pixels=16,
                          keep_components=True, file_name=None, tmpdir=None,
                          **kwargs):
        kwargs.setdefault('oe', True)
        kwargs.setdefault('keep_invalid', False)
        kwargs.setdefault('log', True)

        if window is not None:
            bin_size = hic.bin_size
            pixels = np.round(window/bin_size)

        component_regions = []
        component_matrices = []
        shape = (pixels, pixels)
        counter_matrix = np.zeros(shape)
        matrix_sum = np.zeros(shape)
        for (r1, r2), m in _loop_matrix_iterator(hic, pair_regions, pixels=pixels, **kwargs):
            if hasattr(m, 'mask'):
                inverted_mask = ~m.mask
                counter_matrix += inverted_mask.astype('int')
            else:
                counter_matrix += np.ones(shape)
            matrix_sum += m

            component_regions.append((r1, r2))
            if keep_components:
                component_matrices.append(m)

        am = matrix_sum/counter_matrix

        aggregate_object = cls(file_name=file_name, mode='w', tmpdir=tmpdir)
        aggregate_object.matrix(am)
        aggregate_object.region_pairs(component_regions)
        aggregate_object.components(component_matrices)

        return aggregate_object


def _aggregate_region_bins(hic, region, offset=0):
    region_slice = hic.region_bins(region)
    return region_slice.start-offset, region_slice.stop-offset


def extract_submatrices(matrix, region_pairs, oe=False,
                        log=True, cache=True, mask_inf=True,
                        keep_invalid=False, orient_strand=False,
                        **kwargs):
    cl = matrix.chromosome_lengths
    cb = matrix.chromosome_bins

    valid_region_pairs = defaultdict(list)
    invalid_region_pairs = list()
    logger.info("Checking region pair validity...")
    valid, invalid = 0, 0
    for ix, (region1, region2) in enumerate(region_pairs):
        is_invalid = False
        if region1 is None or region2 is None:
            is_invalid = True
        elif region1.start < 1 or region1.chromosome not in cl or region1.end > cl[region1.chromosome]:
            is_invalid = True
        elif region2.start < 1 or region2.chromosome not in cl or region2.end > cl[region2.chromosome]:
            is_invalid = True
        if is_invalid:
            invalid += 1
            invalid_region_pairs.append((ix, region1, region2))
        else:
            valid += 1
            valid_region_pairs[(region1.chromosome, region2.chromosome)].append((ix, region1, region2))
    logger.info("{}/{} region pairs are invalid".format(invalid, valid + invalid))

    intra_expected, inter_expected = dict(), None
    if oe:
        logger.debug("Calculating expected values...")
        _, intra_expected, inter_expected = matrix.expected_values()

    order = []
    matrices = []
    final_regions = []
    with RareUpdateProgressBar(max_value=valid, prefix='Matrices') as pb:
        current_matrix = 0
        for (chromosome1, chromosome2), regions_pairs_by_chromosome in valid_region_pairs.items():
            if cache:
                sub_matrix = matrix.matrix((chromosome1, chromosome2), **kwargs)
                offset1 = cb[chromosome1][0]
                offset2 = cb[chromosome2][0]
            else:
                sub_matrix = matrix
                offset1 = 0
                offset2 = 0

            for (region_ix, region1, region2) in regions_pairs_by_chromosome:
                final_regions.append((region1, region2))
                current_matrix += 1
                region1_bins = _aggregate_region_bins(matrix, region1, offset1)
                region2_bins = _aggregate_region_bins(matrix, region2, offset2)

                if cache:
                    ms = sub_matrix[region1_bins[0]:region1_bins[1], region2_bins[0]: region2_bins[1]]
                    m = ms.copy()
                    del ms
                else:
                    s1 = slice(region1_bins[0], region1_bins[1])
                    s2 = slice(region2_bins[0], region2_bins[1])
                    m = matrix.matrix((s1, s2), **kwargs)

                if oe:
                    e = np.ones(m.shape)
                    if chromosome1 != chromosome2:
                        e.fill(inter_expected)
                    else:
                        for i, row in enumerate(range(region1_bins[0], region1_bins[1])):
                            for j, col in enumerate(range(region2_bins[0], region2_bins[1])):
                                ix = abs(col - row)
                                e[i, j] = intra_expected[chromosome1][ix]

                    if log:
                        m = np.log2(m/e)
                        m[np.isnan(m)] = 0.
                    else:
                        m = m/e
                        m[np.isnan(m)] = 1

                if mask_inf:
                    m_mask = np.isinf(m)
                    if not hasattr(m, 'mask'):
                        m = np.ma.masked_where(m_mask, m)
                    m.mask += m_mask

                if orient_strand and region1.is_reverse() and region2.is_reverse():
                    m = np.flip(np.flip(m, 0), 1)

                pb.update(current_matrix)
                matrices.append(m)
                order.append(region_ix)

            if cache:
                del sub_matrix

    if keep_invalid:
        for region_ix, r1, r2 in invalid_region_pairs:
            matrices.append(None)
            order.append(region_ix)
            final_regions.append((r1, r2))

    final_regions = [final_regions[ix] for ix in np.argsort(order)]
    matrices = [matrices[ix] for ix in np.argsort(order)]

    return zip(final_regions, matrices)


def _rescale_oe_matrix(matrix, bin_size, scaling_exponent=-0.25):
    rm = np.zeros(matrix.shape)
    b = bin_size
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            v = (abs(i - j) * b + b) ** scaling_exponent
            try:
                if matrix.mask[i, j]:
                    continue
            except AttributeError:
                pass
            rm[i, j] = v * matrix[i, j]
            rm[j, i] = v * matrix[j, i]
    return rm


def aggregate_boundaries(hic, boundary_regions, window=200000,
                         rescale=False, scaling_exponent=-0.25,
                         **kwargs):
    kwargs.setdefault('norm', True)
    kwargs.setdefault('keep_invalid', False)
    kwargs.setdefault('log', True)

    region_pairs = []
    for region in boundary_regions:
        new_start = int(region.center - int(window / 2))
        new_end = int(region.center + int(window / 2))
        new_region = GenomicRegion(chromosome=region.chromosome, start=new_start, end=new_end,
                                   strand=region.strand)
        region_pairs.append((new_region, new_region))

    counter_matrix = None
    matrix_sum = None
    for m in extract_submatrices(hic, region_pairs, **kwargs):
        if counter_matrix is None:
            shape = m.shape
            counter_matrix = np.zeros(shape)
            matrix_sum = np.zeros(shape)

        if hasattr(m, 'mask'):
            inverted_mask = ~m.mask
            counter_matrix += inverted_mask.astype('int')
        else:
            counter_matrix += np.ones(counter_matrix.shape)

        matrix_sum += m

    am = matrix_sum / counter_matrix

    if rescale:
        am = _rescale_oe_matrix(am, hic.bin_size, scaling_exponent=scaling_exponent)

    return am


def _tad_matrix_iterator(hic, tad_regions, absolute_extension=0, relative_extension=1., **kwargs):
    region_pairs = []
    for region in tad_regions:
        new_region = region.expand(absolute=absolute_extension, relative=relative_extension)
        region_pairs.append((new_region, new_region))

    for pair, m in extract_submatrices(hic, region_pairs, **kwargs):
        yield pair, m


def aggregate_tads(hic, tad_regions, pixels=90, rescale=False, scaling_exponent=-0.25,
                   interpolation='nearest', keep_mask=True,
                   absolute_extension=0, relative_extension=1.0,
                   **kwargs):
    kwargs.setdefault('norm', True)
    kwargs.setdefault('keep_invalid', False)
    kwargs.setdefault('log', True)

    shape = (pixels, pixels)
    counter_matrix = np.zeros(shape)
    matrix_sum = np.zeros(shape)
    for m in _tad_matrix_iterator(hic, tad_regions,
                                  absolute_extension=absolute_extension,
                                  relative_extension=relative_extension, **kwargs):
        ms = imresize(m, shape, interp=interpolation, mode='F')

        if keep_mask and hasattr(ms, 'mask'):
            mask = imresize(m.mask, shape, interp='nearest').astype('bool')
            ms = np.ma.masked_where(mask, ms)
            inverted_mask = ~mask
            counter_matrix += inverted_mask.astype('int')
        else:
            counter_matrix += np.ones(shape)

        matrix_sum += ms

    am = matrix_sum/counter_matrix

    if rescale:
        am = _rescale_oe_matrix(am, hic.bin_size, scaling_exponent=scaling_exponent)

    return am


def tad_strength(hic, tad_regions, **kwargs):
    kwargs.setdefault('norm', True)
    kwargs.setdefault('keep_invalid', False)
    kwargs.setdefault('log', False)
    kwargs['relative_extension'] = 1.
    kwargs['absolute_extension'] = 0

    tad_strengths = []
    for m in _tad_matrix_iterator(hic, tad_regions, **kwargs):
        tl = int(m.shape[0]/3)
        upper_third = slice(0, tl)
        middle_third = slice(tl, 2*tl)
        lower_third = slice(2*tl, m.shape[0])
        tad_sum = m[middle_third, middle_third].sum() / np.logical_not(m.mask)[middle_third, middle_third].sum()
        upper_sum = m[upper_third, middle_third].sum() / np.logical_not(m.mask)[upper_third, middle_third].sum()
        lower_sum = m[lower_third, upper_third].sum() / np.logical_not(m.mask)[lower_third, upper_third].sum()
        ts = float(tad_sum / ((upper_sum + lower_sum) / 2))
        tad_strengths.append(np.log2(ts))
    return tad_strengths


def _loop_regions_from_bedpe(bedpe):
    anchors = []
    for region in bedpe.regions:
        a1 = GenomicRegion(chromosome=region.chromosome1, start=region.start1, end=region.end1)
        a2 = GenomicRegion(chromosome=region.chromosome2, start=region.start2, end=region.end2)
        anchors.append((a1, a2))
    return anchors


def _loop_matrix_iterator(hic, loop_regions, pixels=16, **kwargs):
    left = int(pixels / 2)
    right = left if pixels % 2 == 1 else left - 1

    if isinstance(loop_regions, Bedpe):
        loop_regions = _loop_regions_from_bedpe(loop_regions)

    bin_size = hic.bin_size
    region_pairs = []
    invalid = 0
    for (anchor1, anchor2) in loop_regions:
        a1 = GenomicRegion(chromosome=anchor1.chromosome, start=anchor1.center, end=anchor1.center)
        a2 = GenomicRegion(chromosome=anchor2.chromosome, start=anchor2.center, end=anchor2.center)

        try:
            r1 = list(hic.regions(a1))[0].copy()
            r2 = list(hic.regions(a2))[0].copy()
            r1.start -= left * bin_size
            r1.end += right * bin_size
            r2.start -= left * bin_size
            r2.end += right * bin_size
            region_pairs.append((r1, r2))
        except IndexError:
            invalid += 1
            region_pairs.append((None, None))

    if invalid > 0:
        logger.warning("{} region pairs invalid, most likely due to missing chromosome data".format(invalid))

    for pair, m in extract_submatrices(hic, region_pairs, **kwargs):
        yield pair, m


def aggregate_loops(hic, loop_regions, pixels=16, **kwargs):
    kwargs.setdefault('norm', True)
    kwargs.setdefault('keep_invalid', False)
    kwargs.setdefault('log', True)

    shape = (pixels, pixels)
    counter_matrix = np.zeros(shape)
    matrix_sum = np.zeros(shape)
    for m in _loop_matrix_iterator(hic, loop_regions, pixels=pixels, **kwargs):
        if hasattr(m, 'mask'):
            inverted_mask = ~m.mask
            counter_matrix += inverted_mask.astype('int')
        else:
            counter_matrix += np.ones(shape)
        matrix_sum += m

    return matrix_sum/counter_matrix


def loop_strength(hic, loop_regions, pixels=16, **kwargs):
    kwargs.setdefault('log', False)
    kwargs.setdefault('norm', True)
    kwargs['keep_invalid'] = True

    if isinstance(loop_regions, Bedpe):
        loop_regions = _loop_regions_from_bedpe(loop_regions)

    # generating new regions
    new_region_pairs = []  # 0: original, 1: control left, 2: control right
    for (region1, region2) in loop_regions:
        d = int(abs(region1.center - region2.center))
        new_left = GenomicRegion(chromosome=region1.chromosome, start=region1.start - d, end=region1.end - d)
        new_right = GenomicRegion(chromosome=region1.chromosome, start=region2.start + d, end=region2.end + d)
        new_region_pairs.append((region1, region2))
        new_region_pairs.append((new_left, region1))
        new_region_pairs.append((region2, new_right))

    original, left, right = [], [], []
    for i, m in enumerate(_loop_matrix_iterator(hic, new_region_pairs, pixels=pixels, **kwargs)):
        if m is not None:
            value = float(m.sum()/np.logical_not(m.mask).sum())
        else:
            value = None

        if i % 3 == 0:
            original.append(value)
        elif i % 3 == 1:
            left.append(value)
        else:
            right.append(value)

    ratios = []
    for i in range(len(original)):
        if original[i] is None:
            continue
        if left[i] is None and right[i] is None:
            continue

        if left[i] is None:
            r = original[i]/right[i]
        elif right[i] is None:
            r = original[i]/left[i]
        else:
            r = original[i]/((left[i]+right[i])/2)
        ratios.append(np.log2(r))
    return ratios


def contact_directionality_bias(hic, regions, distance=1000000, region_anchor='center', **kwargs):
    forward_region_pairs = []
    reverse_region_pairs = []
    for region in regions:
        pos = int(getattr(region, region_anchor))
        new_region = GenomicRegion(chromosome=region.chromosome, start=pos, end=pos, strand=region.strand)
        if region.is_forward():
            forward_region_pairs.append((new_region, new_region.expand(absolute=distance)))
        else:
            reverse_region_pairs.append((new_region, new_region.expand(absolute=distance)))

    cumulative_forward = np.zeros(int(distance / hic.bin_size) * 2 + 1)
    count_forward = np.zeros(int(distance / hic.bin_size) * 2 + 1)
    for matrix in extract_submatrices(hic, forward_region_pairs, **kwargs):
        cumulative_forward += matrix[0, :]
        if hasattr(matrix, 'mask'):
            inverted_mask = ~matrix.mask
            count_forward += inverted_mask.astype('int')[0, :]
        else:
            count_forward += np.ones(count_forward.shape)

    cumulative_reverse = np.zeros(int(distance / hic.bin_size) * 2 + 1)
    count_reverse = np.zeros(int(distance / hic.bin_size) * 2 + 1)
    for matrix in extract_submatrices(hic, reverse_region_pairs, **kwargs):
        cumulative_reverse += matrix[0, :]
        if hasattr(matrix, 'mask'):
            inverted_mask = ~matrix.mask
            count_reverse += inverted_mask.astype('int')[0, :]
        else:
            count_reverse += np.ones(count_reverse.shape)

    avg_forward = cumulative_forward / count_forward
    avg_reverse = cumulative_reverse / count_reverse

    bin_size = hic.bin_size
    d = []
    ratio_forward = []
    ratio_reverse = []
    center = int(len(avg_forward)/2)
    for ix in range(center + 1):
        d.append(ix * bin_size)
        ratio_forward.append(avg_forward[center + ix] / avg_forward[center - ix])
        ratio_reverse.append(avg_reverse[center + ix] / avg_reverse[center - ix])

    return d, ratio_forward, ratio_reverse