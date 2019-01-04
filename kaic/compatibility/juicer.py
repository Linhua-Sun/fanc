import logging
import struct
from ..matrix import RegionMatrixContainer, Edge
from genomic_regions import GenomicRegion
import numpy as np
import warnings
import zlib

logger = logging.getLogger(__name__)


def _read_cstr(f):
    """
    Copyright (c) 2016 Aiden Lab

    :param f: open binary file
    :return: str
    """
    buf = ""
    while True:
        b = f.read(1)
        b = b.decode('utf-8', 'backslashreplace')
        if b is None or b == '\0':
            return str(buf)
        else:
            buf = buf + b


class JuicerHic(RegionMatrixContainer):
    def __init__(self, hic_file, resolution, normalisation='NONE'):
        RegionMatrixContainer.__init__(self)
        self._hic_file = hic_file

        bp_resolutions, _ = self.resolutions()
        if resolution not in bp_resolutions:
            raise ValueError("Resolution {} not supported ({})".format(resolution, bp_resolutions))
        self._resolution = resolution
        self._normalisation = normalisation
        self._unit = 'BP'

        with open(self._hic_file, 'rb') as req:
            magic_string = struct.unpack('<3s', req.read(3))[0]
            if magic_string != b"HIC":
                raise ValueError("File {} does not seem to be a .hic "
                                 "file produced with juicer!".format(hic_file))

    @property
    def version(self):
        with open(self._hic_file, 'rb') as req:
            req.read(4)  # jump to version location
            return struct.unpack('<i', req.read(4))[0]

    def _master_index(self):
        with open(self._hic_file, 'rb') as req:
            req.read(8)  # jump to master index location
            return struct.unpack('<q', req.read(8))[0]

    @staticmethod
    def _skip_to_attributes(req):
        req.seek(0)

        # skip magic, version, master
        req.read(16)

        # skip genome
        while req.read(1).decode("utf-8") != '\0':
            pass

    @staticmethod
    def _skip_to_chromosome_lengths(req):
        JuicerHic._skip_to_attributes(req)

        # skip attributes
        n_attributes = struct.unpack('<i', req.read(4))[0]
        for _ in range(0, n_attributes):
            # skip key
            while req.read(1).decode("utf-8", "backslashreplace") != '\0':
                pass
            # skip value
            while req.read(1).decode("utf-8", "backslashreplace") != '\0':
                pass

    @staticmethod
    def _skip_to_resolutions(req):
        JuicerHic._skip_to_chromosome_lengths(req)

        n_chromosomes = struct.unpack('<i', req.read(4))[0]
        for _ in range(0, n_chromosomes):
            while req.read(1).decode("utf-8", "backslashreplace") != '\0':
                pass
            req.read(4)

    @staticmethod
    def _skip_to_chromosome_sites(req):
        JuicerHic._skip_to_resolutions(req)

        n_resolutions = struct.unpack('<i', req.read(4))[0]
        for _ in range(0, n_resolutions):
            req.read(4)

        n_fragment_resolutions = struct.unpack('<i', req.read(4))[0]
        for _ in range(0, n_fragment_resolutions):
            req.read(4)

    @property
    def juicer_attributes(self):
        with open(self._hic_file, 'rb') as req:
            JuicerHic._skip_to_attributes(req)
            attributes = {}
            n_attributes = struct.unpack('<i', req.read(4))[0]
            for _ in range(0, n_attributes):
                key = _read_cstr(req)
                value = _read_cstr(req)
                attributes[key] = value
        return attributes

    @property
    def chromosome_lengths(self):
        with open(self._hic_file, 'rb') as req:
            JuicerHic._skip_to_chromosome_lengths(req)

            chromosome_lengths = {}
            n_chromosomes = struct.unpack('<i', req.read(4))[0]
            for _ in range(0, n_chromosomes):
                name = _read_cstr(req)
                length = struct.unpack('<i', req.read(4))[0]
                chromosome_lengths[name] = length

        return chromosome_lengths

    def chromosomes(self):
        with open(self._hic_file, 'rb') as req:
            JuicerHic._skip_to_chromosome_lengths(req)

            chromosomes = []
            n_chromosomes = struct.unpack('<i', req.read(4))[0]
            for _ in range(0, n_chromosomes):
                name = _read_cstr(req)
                req.read(4)
                if name != 'All':
                    chromosomes.append(name)

        return chromosomes

    def resolutions(self):
        with open(self._hic_file, 'rb') as req:
            JuicerHic._skip_to_resolutions(req)

            resolutions = []
            n_resolutions = struct.unpack('<i', req.read(4))[0]
            for _ in range(0, n_resolutions):
                resolution = struct.unpack('<i', req.read(4))[0]
                resolutions.append(resolution)

            fragment_resolutions = []
            n_fragment_resolutions = struct.unpack('<i', req.read(4))[0]
            for _ in range(0, n_fragment_resolutions):
                resolution = struct.unpack('<i', req.read(4))[0]
                fragment_resolutions.append(resolution)

            return resolutions, fragment_resolutions

    @staticmethod
    def _skip_to_footer(req):
        req.seek(0)
        req.read(8)  # jump to master index location
        master_index = struct.unpack('<q', req.read(8))[0]

        # jump to footer location
        req.seek(master_index)
        req.read(4)  # skip number of bytes

    @staticmethod
    def _skip_to_expected_values(req):
        JuicerHic._skip_to_footer(req)

        n_entries = struct.unpack('<i', req.read(4))[0]
        for _ in range(n_entries):
            while req.read(1).decode("utf-8", "backslashreplace") != '\0':
                pass
            req.read(12)

    @staticmethod
    def _skip_to_normalised_expected_values(req):
        JuicerHic._skip_to_expected_values(req)

        n_vectors = struct.unpack('<i', req.read(4))[0]
        for _ in range(n_vectors):
            while req.read(1).decode("utf-8", "backslashreplace") != '\0':
                pass
            req.read(4)

            n_values = struct.unpack('<i', req.read(4))[0]
            for j in range(n_values):
                req.read(8)

            n_scaling_factors = struct.unpack('<i', req.read(4))[0]
            for _ in range(n_scaling_factors):
                req.read(12)

    @staticmethod
    def _skip_to_normalisation_vectors(req):
        JuicerHic._skip_to_normalised_expected_values(req)

        n_vectors = struct.unpack('<i', req.read(4))[0]
        for _ in range(n_vectors):
            while req.read(1).decode("utf-8", "backslashreplace") != '\0':
                pass
            while req.read(1).decode("utf-8", "backslashreplace") != '\0':
                pass
            req.read(4)

            n_values = struct.unpack('<i', req.read(4))[0]
            for j in range(n_values):
                req.read(8)

            n_scaling_factors = struct.unpack('<i', req.read(4))[0]
            for _ in range(n_scaling_factors):
                req.read(12)

    def _matrix_positions(self):
        """
        Copyright (c) 2016 Aiden Lab
        """

        with open(self._hic_file, 'rb') as req:
            JuicerHic._skip_to_footer(req)

            chromosome_pair_positions = {}
            n_entries = struct.unpack('<i', req.read(4))[0]
            for _ in range(n_entries):
                key = tuple(_read_cstr(req).split('_'))
                file_position = struct.unpack('<q', req.read(8))[0]
                req.read(4)  # skip size in bytes
                chromosome_pair_positions[key] = file_position
            return chromosome_pair_positions

    @staticmethod
    def _expected_value_vectors_from_pos(req, normalisation=None):
        expected_values = dict()
        scaling_factors = dict()

        n_vectors = struct.unpack('<i', req.read(4))[0]
        for _ in range(n_vectors):
            while req.read(1).decode("utf-8", "backslashreplace") != '\0':
                pass
            if normalisation:
                entry_normalisation = _read_cstr(req)
            else:
                entry_normalisation = 'NONE'

            bin_size = struct.unpack('<i', req.read(4))[0]

            expected_values[bin_size] = []
            ev = []
            n_values = struct.unpack('<i', req.read(4))[0]
            for j in range(n_values):
                v = struct.unpack('<d', req.read(8))[0]
                ev.append(v)

            if normalisation is None or entry_normalisation == normalisation:
                expected_values[bin_size] = ev

            scaling_factors[bin_size] = dict()
            sf = dict()
            n_scaling_factors = struct.unpack('<i', req.read(4))[0]
            for _ in range(n_scaling_factors):
                chromosome_index = struct.unpack('<i', req.read(4))[0]
                f = struct.unpack('<d', req.read(8))[0]
                sf[chromosome_index] = f

            if normalisation is None or entry_normalisation == normalisation:
                scaling_factors[bin_size] = sf

        return expected_values, scaling_factors

    def expected_value_vector(self, chromosome, normalisation=None, resolution=None):
        if normalisation is None:
            normalisation = self._normalisation

        if resolution is None:
            resolution = self._resolution

        chromosome_ix = self.chromosomes().index(chromosome)

        if normalisation == 'NONE':
            vectors, scaling_factors = self.expected_value_vectors()
            return np.array(vectors[resolution]) * scaling_factors[resolution][chromosome_ix]
        else:
            vectors, scaling_factors = self.normalised_expected_value_vectors(normalisation)
            return np.array(vectors[resolution]) * scaling_factors[resolution][chromosome_ix]

    def expected_value_vectors(self):
        with open(self._hic_file, 'rb') as req:
            JuicerHic._skip_to_expected_values(req)

            return JuicerHic._expected_value_vectors_from_pos(req)

    def normalised_expected_value_vectors(self, normalisation=None):
        if normalisation is None:
            normalisation = self._normalisation

        with open(self._hic_file, 'rb') as req:
            JuicerHic._skip_to_normalised_expected_values(req)

            return JuicerHic._expected_value_vectors_from_pos(req, normalisation=normalisation)

    def normalisation_vector(self, chromosome, normalisation=None, resolution=None, unit=None):
        if resolution is None:
            resolution = self._resolution

        if normalisation is None:
            normalisation = self._normalisation

        if normalisation == 'NONE':
            bins = np.ceil(self.chromosome_lengths[chromosome]/resolution)
            return [1.0] * bins

        if unit is None:
            unit = self._unit

        chromosomes = self.chromosomes()
        chromosome_index = chromosomes.index(chromosome) + 1

        with open(self._hic_file, 'rb') as req:
            JuicerHic._skip_to_normalisation_vectors(req)

            n_entries = struct.unpack('<i', req.read(4))[0]
            for _ in range(n_entries):
                entry_normalisation = _read_cstr(req)
                entry_chromosome_index = struct.unpack('<i', req.read(4))[0]
                entry_unit = _read_cstr(req)
                entry_resolution = struct.unpack('<i', req.read(4))[0]
                file_position = struct.unpack('<q', req.read(8))[0]
                req.read(4)  # skip size in bytes

                if (entry_chromosome_index == chromosome_index and
                        entry_normalisation == normalisation and
                        entry_resolution == resolution and
                        entry_unit == unit):
                    req.seek(file_position)
                    vector = []
                    n_values = struct.unpack('<i', req.read(4))[0]
                    for _ in range(n_values):
                        v = struct.unpack('<d', req.read(8))[0]
                        vector.append(v)

                    return vector
        raise ValueError("Cannot find normalisation vector that matches "
                         "chromosome: {}, normalisation: {}, "
                         "resolution: {}, unit: {}".format(chromosome, normalisation, resolution, unit))

    def _chromosome_ix_offset(self, target_chromosome):
        chromosome_lengths = self.chromosome_lengths
        if target_chromosome not in chromosome_lengths:
            raise ValueError("Chromosome {} not in matrix.".format(target_chromosome))

        offset_ix = 0
        for chromosome, chromosome_length in chromosome_lengths.items():
            if chromosome == 'All':
                continue

            if target_chromosome == chromosome:
                return offset_ix

            ixs = int(np.ceil(chromosome_length / self._resolution))
            offset_ix += ixs

    def _region_start(self, region):
        region = self._convert_region(region)
        offset_ix = self._chromosome_ix_offset(region.chromosome)
        region_start = region.start if region.start is not None else 1
        ix = int((region_start - 1) / self._resolution)
        start = self._resolution * ix + 1
        return offset_ix + ix, start

    def _region_iter(self, *args, **kwargs):
        current_region_index = 0
        for chromosome, chromosome_length in self.chromosome_lengths.items():
            if chromosome == 'All':
                continue
            norm = self.normalisation_vector(chromosome)
            for i, start in enumerate(range(1, chromosome_length, self._resolution)):
                end = min(start + self._resolution - 1, chromosome_length)
                region = GenomicRegion(chromosome=chromosome, start=start,
                                       end=end, bias=norm[i],
                                       ix=current_region_index)
                current_region_index += 1
                yield region

    def _region_subset(self, region, *args, **kwargs):
        subset_ix, subset_start = self._region_start(region)

        cl = self.chromosome_lengths[region.chromosome]
        norm = self.normalisation_vector(region.chromosome)
        for i, start in enumerate(range(subset_start, region.end, self._resolution)):
            end = min(start + self._resolution - 1, cl, region.end)
            bias_ix = int(start / self._resolution)
            r = GenomicRegion(chromosome=region.chromosome, start=start,
                              end=end, bias=norm[bias_ix],
                              ix=int(subset_ix + i))
            yield r

    def _region_len(self):
        length = 0
        for chromosome, chromosome_length in self.chromosome_lengths.items():
            if chromosome == 'All':
                continue

            ixs = int(np.ceil(chromosome_length / self._resolution))
            length += ixs
        return length

    def _read_block(self, req, file_position, block_size_in_bytes):
        req.seek(file_position)
        block_compressed = req.read(block_size_in_bytes)
        block = zlib.decompress(block_compressed)

        n_records = struct.unpack('<i', block[0:4])[0]
        if self.version < 7:
            for i in range(n_records):
                x = struct.unpack('<i', block[(12 * i + 4):(12 * i + 8)])[0]
                y = struct.unpack('<i', block[(12 * i + 8):(12 * i + 12)])[0]
                weight = struct.unpack('<f', block[(12 * i + 12):(12 * i + 16)])[0]
                yield x, y, weight
        else:
            x_offset = struct.unpack('<i', block[4:8])[0]
            y_offset = struct.unpack('<i', block[8:12])[0]
            use_short = not struct.unpack('<b', block[12:13])[0] == 0
            block_type = struct.unpack('<b', block[13:14])[0]
            index = 0

            if block_type == 1:
                row_count = struct.unpack('<h', block[14:16])[0]
                temp = 16
                for i in range(row_count):
                    y_raw = struct.unpack('<h', block[temp:(temp + 2)])[0]
                    temp += 2
                    y = y_raw + y_offset
                    col_count = struct.unpack('<h', block[temp:(temp + 2)])[0]
                    temp += 2
                    for j in range(col_count):
                        x_raw = struct.unpack('<h', block[temp:(temp + 2)])[0]
                        temp += 2
                        x = x_offset + x_raw
                        if not use_short:
                            weight = struct.unpack('<h', block[temp:(temp + 2)])[0]
                            temp += 2
                        else:
                            weight = struct.unpack('<f', block[temp:(temp + 4)])[0]
                            temp += 4
                        yield x, y, weight

                        index += 1
            elif block_type == 2:
                temp = 14
                n_points = struct.unpack('<i', block[temp:(temp + 4)])[0]
                temp += 4
                w = struct.unpack('<h', block[temp:(temp + 2)])[0]
                temp += 2
                for i in range(n_points):
                    row = int(i / w)
                    col = i - row * w
                    x = int(x_offset + col)
                    y = int(y_offset + row)
                    if not use_short:
                        weight = struct.unpack('<h', block[temp:(temp + 2)])[0]
                        temp += 2
                        if weight != -32768:
                            yield x, y, weight
                            index += 1
                    else:
                        weight = struct.unpack('<f', block[temp:(temp + 4)])[0]
                        temp += 4
                        if weight != 0x7fc00000:
                            yield x, y, weight
                            index = index + 1

    def _read_matrix(self, region1, region2):
        region1 = self._convert_region(region1)
        region2 = self._convert_region(region2)

        chromosomes = self.chromosomes()
        chromosome1_ix = chromosomes.index(region1.chromosome)
        chromosome2_ix = chromosomes.index(region2.chromosome)

        if chromosome1_ix > chromosome2_ix:
            region1, region2 = region2, region1

        region1_chromosome_offset = self._chromosome_ix_offset(region1.chromosome)
        region2_chromosome_offset = self._chromosome_ix_offset(region2.chromosome)

        matrix_file_position = self._matrix_positions()[(region1.chromosome, region2.chromosome)]

        with open(self._hic_file, 'rb') as req:
            req.seek(matrix_file_position)
            req.read(8)  # skip chromosome index

            block_bin_count = None
            block_column_count = None
            block_map = dict()
            n_resolutions = struct.unpack('<i', req.read(4))[0]
            for i in range(n_resolutions):
                unit = _read_cstr(req)
                req.read(20)  # skip reserved but unused fields

                bin_size = struct.unpack('<i', req.read(4))[0]
                if unit == self._unit and bin_size == self._resolution:
                    block_bin_count = struct.unpack('<i', req.read(4))[0]
                    block_column_count = struct.unpack('<i', req.read(4))[0]

                    n_blocks = struct.unpack('<i', req.read(4))[0]
                    for b in range(n_blocks):
                        block_number = struct.unpack('<i', req.read(4))[0]
                        file_position = struct.unpack('<q', req.read(8))[0]
                        block_size_in_bytes = struct.unpack('<i', req.read(4))[0]
                        block_map[block_number] = (file_position, block_size_in_bytes)
                else:
                    req.read(8)

                    n_blocks = struct.unpack('<i', req.read(4))[0]
                    for b in range(n_blocks):
                        req.read(16)

            if block_bin_count is None or block_column_count is None:
                raise ValueError("Matrix data for {} {} not found!".format(self._resolution, self._unit))

            region1_bins = int(region1.start / self._resolution), int(region1.end / self._resolution) + 1
            region2_bins = int(region2.start / self._resolution), int(region2.end / self._resolution) + 1

            col1, col2 = int(region1_bins[0] / block_bin_count), int(region1_bins[1] / block_bin_count)
            row1, row2 = int(region2_bins[0] / block_bin_count), int(region2_bins[1] / block_bin_count)

            blocks = set()
            for r in range(row1, row2 + 1):
                for c in range(col1, col2 + 1):
                    block_number = r * block_column_count + c
                    blocks.add(block_number)

            if region1.chromosome == region2.chromosome:
                for r in range(col1, col2 + 1):
                    for c in range(row1, row2 + 1):
                        block_number = r * block_column_count + c
                        blocks.add(block_number)

            for block_number in blocks:
                try:
                    file_position, block_size_in_bytes = block_map[block_number]

                    for x, y, weight in self._read_block(req, file_position, block_size_in_bytes):
                        if x > y:
                            raise ValueError("X/Y not in correct order")

                        if region1_bins[0] <= x < region1_bins[1] - 1 and region2_bins[0] <= y < region2_bins[1] - 1:
                            yield x + region1_chromosome_offset, y + region2_chromosome_offset, weight
                        elif region1.chromosome == region2.chromosome:
                            if region1_bins[0] <= y < region1_bins[1] - 1 and region2_bins[0] <= x < region2_bins[1] - 1:
                                yield x + region1_chromosome_offset, y + region2_chromosome_offset, weight

                except KeyError:
                    logger.debug("Could not find block {}".format(block_number))

    def _edges_subset(self, key=None, row_regions=None, col_regions=None,
                      *args, **kwargs):

        if row_regions[0].chromosome != row_regions[-1].chromosome:
            raise ValueError("Cannot subset rows across multiple chromosomes!")

        if col_regions[0].chromosome != col_regions[-1].chromosome:
            raise ValueError("Cannot subset columns across multiple chromosomes!")

        regions_by_ix = {}
        for region in row_regions + col_regions:
            regions_by_ix[region.ix] = region

        row_span = GenomicRegion(chromosome=row_regions[0].chromosome,
                                 start=row_regions[0].start,
                                 end=row_regions[-1].end)

        col_span = GenomicRegion(chromosome=col_regions[0].chromosome,
                                 start=col_regions[0].start,
                                 end=col_regions[-1].end)

        for x, y, weight in self._read_matrix(row_span, col_span):
            yield Edge(source=regions_by_ix[x],
                       sink=regions_by_ix[y],
                       weight=weight)

    def _edges_iter(self, *args, **kwargs):
        chromosomes = self.chromosomes()
        for ix1 in range(len(chromosomes)):
            chromosome1 = chromosomes[ix1]
            for ix2 in range(ix1, len(chromosomes)):
                chromosome2 = chromosomes[ix2]

                for edge in self.edges((chromosome1, chromosome2), *args, **kwargs):
                    yield edge

