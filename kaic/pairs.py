from __future__ import division

import copy
import gzip
import logging
import multiprocessing as mp
import os
import threading
import uuid
from abc import abstractmethod, ABCMeta
from bisect import bisect_right
from builtins import object
from collections import defaultdict
from queue import Empty
from timeit import default_timer as timer

import msgpack
import numpy as np
import pysam
import tables as t
from future.utils import with_metaclass, viewitems

from genomic_regions import GenomicRegion, RegionBased
from .config import config
from .general import MaskFilter, MaskedTable, Mask
from .hic import Hic
from .matrix import Edge, RegionPairsTable
from .regions import genome_regions
from .tools.general import RareUpdateProgressBar, add_dict, find_alignment_match_positions, WorkerMonitor
from .tools.sambam import natural_cmp

logger = logging.getLogger(__name__)


def generate_pairs(sam1_file, sam2_file, regions,
                   restriction_enzyme=None, read_filters=(),
                   output_file=None, check_sorted=True,
                   threads=1, batch_size=10000000):
    """
    Generate Pairs object from SAM/BAM files.

    This is a convenience function that let's you create a
    Pairs object from SAM/BAM data in a single step.

    :param sam1_file: Path to a sorted SAM/BAM file (1st mate)
    :param sam2_file: Path to a sorted SAM/BAM file (2nd mate)
    :param regions: Path to file with restriction fragments (BED, GFF)
                    or FASTA with genomic sequence
    :param restriction_enzyme: Name of restriction enzyme
                               (only when providing FASTA as regions)
    :param read_filters: List of :class:`~ReadFilter` to filter reads
                         while loading from SAM/BAM
    :param output_file: Path to output file
    :param check_sorted: Double-check that input SAM files
                         are sorted if True (default)
    :param threads: Number of threads used for finding restriction
                    fragments for read pairs
    :param batch_size: Number of read pairs sent to each restriction
                       fragment worker
    :return: :class:`~ReadPairs`
    """
    regions = genome_regions(regions, restriction_enzyme=restriction_enzyme)

    sb = SamBamReadPairGenerator(sam1_file, sam2_file, check_sorted=check_sorted)
    for f in read_filters:
        sb.add_filter(f)

    pairs = ReadPairs(file_name=output_file, mode='w')

    if isinstance(regions, RegionBased):
        pairs.add_regions(regions.regions, preserve_attributes=False)
    else:
        pairs.add_regions(regions, preserve_attributes=False)
    pairs.add_read_pairs(sb, threads=threads, batch_size=batch_size)

    return pairs


class Monitor(WorkerMonitor):
    """
    Class to monitor fragment info worker threads.
    """
    def __init__(self, value=0):
        WorkerMonitor.__init__(self, value=value)
        self.generating_pairs_lock = threading.Lock()

        with self.generating_pairs_lock:
            self.generating_pairs = True

    def set_generating_pairs(self, value):
        """
        Set the pair generating status.
        """
        with self.generating_pairs_lock:
            self.generating_pairs = value

    def is_generating_pairs(self):
        """
        Check the pair generating status.
        """
        with self.generating_pairs_lock:
            return self.generating_pairs


def _fragment_info_worker(monitor, input_queue, output_queue, fi, fe):
    """
    Worker that finds the restriction fragment info for read pairs.

    Finds the restriction fragment each read maps to, and returns the
    coordinates of the read and fragment pairs for each read pair.

    :param monitor: :class:`~Monitor`
    :param input_queue: Queue for input read_pairs
    :param output_queue: Queue for output fragment infos
    :param fi: Fragment info dictionary by chromosome and fragment index
    :param fe: Fragment end coordinates by chromosome
    :return: list of fragment infos
    """
    worker_uuid = uuid.uuid4()
    logger.debug("Starting fragment info worker {}".format(worker_uuid))

    while True:
        # wait for input
        monitor.set_worker_idle(worker_uuid)
        logger.debug("Worker {} waiting for input".format(worker_uuid))
        read_pairs = input_queue.get(True)
        monitor.set_worker_busy(worker_uuid)
        logger.debug('Worker {} reveived input!'.format(worker_uuid))
        read_pairs = msgpack.loads(read_pairs)

        fragment_infos = []
        skipped_counter = 0
        for (chrom1, pos1, flag1), (chrom2, pos2, flag2) in read_pairs:
            chrom1 = chrom1.decode() if isinstance(chrom1, bytes) else chrom1
            chrom2 = chrom2.decode() if isinstance(chrom2, bytes) else chrom2

            try:
                pos_ix1 = bisect_right(fe[chrom1], pos1)
                pos_ix2 = bisect_right(fe[chrom2], pos2)

                f_ix1, f_chromosome_ix1, f_start1, f_end1 = fi[chrom1][pos_ix1]
                f_ix2, f_chromosome_ix2, f_start2, f_end2 = fi[chrom2][pos_ix2]

                r_strand1 = -1 if flag1 & 16 else 1
                r_strand2 = -1 if flag2 & 16 else 1

                fragment_infos.append(
                    ((pos1, r_strand1, f_ix1, f_chromosome_ix1, f_start1, f_end1),
                     (pos2, r_strand2, f_ix2, f_chromosome_ix2, f_start2, f_end2))
                )
            except (KeyError, IndexError):
                skipped_counter += 1
        logger.debug("Worker {} skipped {} pairs".format(worker_uuid, skipped_counter))
        output_queue.put(msgpack.dumps(fragment_infos))
        del read_pairs


def _read_pairs_worker(read_pairs, input_queue, monitor, batch_size=100000):
    """
    Worker to distribute incoming read pairs to fragment info workers.

    :param read_pairs: Iterator of read tuples (read1, read2)
    :param input_queue: Input queue for read pairs
    :param monitor: :class:`~Monitor`
    :param batch_size: Number of read pairs sent to each worker
    """
    logger.debug("Starting read pairs worker")
    try:
        read_pairs_batch = []
        for read1, read2 in read_pairs:
            read_pairs_batch.append((
                (read1.reference_name, read1.pos, read1.flag),
                (read2.reference_name, read2.pos, read2.flag)
            ))
            if len(read_pairs_batch) >= batch_size:
                logger.debug("Submitting read pair batch ({}) to input queue".format(batch_size))
                input_queue.put(msgpack.dumps(read_pairs_batch))
                read_pairs_batch = []
                monitor.increment()
        if len(read_pairs_batch) > 0:
            logger.debug("Submitting read pair batch ({}) to input queue".format(batch_size))
            input_queue.put(msgpack.dumps(read_pairs_batch))
            monitor.increment()
    finally:
        monitor.set_generating_pairs(False)
    logger.debug("Terminating read pairs worker")


class MinimalRead(object):
    """
    Minimal class representing an aligned read.

    .. attribute:: chromosome

        Chromosome name

    .. attribute:: reference_name

        Identical to chromosome, exists for
        compatibility with pysam

    .. attribute:: position

        Position of aligned read on chromosome

    .. attribute:: pos

        Identical to position, exists for
        compatibility with Pysam

    .. attribute:: strand

        Strand of the alignment

    .. attribute:: flag

        Flag representing the strandedness of a read.
        0 if on forward strand, else -1
    """

    def __init__(self, chromosome, position, strand):
        self.chromosome = chromosome
        self.reference_name = chromosome
        self.position = position
        self.strand = strand
        self.flag = 0 if strand == '+' or strand == 1 else -1
        self.pos = position
        self.tags = {}

    def get_tag(self, tag):
        raise KeyError("tag '{}' not present".format(tag))


class ReadPairGenerator(object):
    """
    Base class for generating and filtering read pairs.

    This class primarily provides filtering capabilities for
    read pairs generated by subclasses of :class:`~ReadPairGenerator`.
    You can add a :class:`~ReadFilter` using :func:`~ReadPairGenerator.add_filter`,
    which will be used during read pair generation. Filtering statistics
    are collected during the run, and can be obtained via
    :func:`~ReadPairGenerator.stats`.

    These generators are primarily meant as input for
    :func:`~ReadPairs.add_read_pairs`, but can also be used
    independently.

    Subclasses of :class:`~ReadPairGenerator` must implement the
    :func:`~ReadPairGenerator._iter_read_pairs` function.
    """
    def __init__(self):
        self.filters = []
        self._filter_stats = defaultdict(int)
        self._total_pairs = 0
        self._valid_pairs = 0

        unmapped_filter = UnmappedFilter(mask=Mask('unmappable', 'Mask unmapped reads',
                                                   ix=len(self.filters)))
        self.add_filter(unmapped_filter)
        self._unmapped_filter_ix = len(self.filters) - 1

    def _iter_read_pairs(self, *args, **kwargs):
        raise NotImplementedError("Class must override iter_read_pairs")

    def add_filter(self, read_filter):
        """
        Add a :class:`~ReadFilter` to this object.

        The filter will be applied during read pair generation.

        :param read_filter: :class:`~ReadFilter`
        """
        if not isinstance(read_filter, ReadFilter):
            raise ValueError("argument must be an instance of class ReadFilter!")
        self.filters.append(read_filter)

    def stats(self):
        """
        Return filter statistics collected during read pair generation.

        The :func:`~ReadPairGenerator.__iter__` filters reads based on the
        filters added using :func:`~ReadPairGenerator.add_filter`. During
        filtering, it keeps track of the numbers of read pairs that were
        filtered. This function returns a :class:`~dict` of the form
        <filter_name>: <number of reads filtered out>.

        :return: dict
        """
        filter_names = []
        for i, f in enumerate(self.filters):
            if hasattr(f, 'mask') and f.mask is not None:
                filter_names.append(f.mask.name)
            else:
                filter_names.append('filter_{}'.format(i))

        stats = dict()
        for i, count in self._filter_stats.items():
            stats[filter_names[i]] = count
        stats['valid'] = self._valid_pairs
        stats['total'] = self._total_pairs
        return stats

    def __iter__(self):
        self._filter_stats = defaultdict(int)
        self._total_pairs = 0
        self._valid_pairs = 0
        for (read1, read2) in self._iter_read_pairs():
            valid_reads = True
            for i, f in enumerate(self.filters):
                if not f.valid_read(read1) or not f.valid_read(read2):
                    self._filter_stats[i] += 1
                    valid_reads = False
            if valid_reads:
                yield (read1, read2)
                self._valid_pairs += 1
            self._total_pairs += 1


class TxtReadPairGenerator(ReadPairGenerator):
    """
    Generate read pairs from a plain text file.

    This is an implementation of :class:`~ReadPairGenerator` for
    reading read pairs from an arbitrary text file. For specific
    text file formats have a look at :class:`~FourDNucleomePairGenerator`
    or :class:`~HicProPairGenerator`.

    :class:`~TxtReadPairGenerator` iterates over lines in
    a file and splits them into fields at "sep". It then extracts
    the chromosome, position and strand for each read in the pair,
    according to the fields specified by "chr<1|2>_field",
    "pos<1|2>_field", and "strand<1|2>_field". If your file does not
    have strand fields, or if you don't want to load them, you can
    simply set them to "None".
    """
    def __init__(self, valid_pairs_file, sep=None,
                 chr1_field=1, pos1_field=2, strand1_field=3,
                 chr2_field=4, pos2_field=5, strand2_field=6):
        """
        Initialise read pair generator.

        :param valid_pairs_file: Path to a txt file with valid pairs.
                                 May be gzipped, in which case the file
                                 name must end with ".gz" or ".gzip"
        :param sep: A field separator. Defaults to whitespace.
        :param chr1_field: int, index of the field which has the information
                           for the first mate's chromosome
        :param pos1_field: int, index of the field which has the information
                           for the first mate's position on the reference in bp
        :param strand1_field: int, index of the field which has the information
                              for the first mate's strand (+/-)
        :param chr2_field: int, index of the field which has the information
                           for the second mate's chromosome
        :param pos2_field: int, index of the field which has the information
                           for the second mate's position on the reference in bp
        :param strand2_field: int, index of the field which has the information
                              for the second mate's strand (+/-)
        """
        ReadPairGenerator.__init__(self)
        self._file_name = valid_pairs_file
        self.sep = sep
        self.chr1_field = chr1_field
        self.pos1_field = pos1_field
        self.strand1_field = strand1_field
        self.chr2_field = chr2_field
        self.pos2_field = pos2_field
        self.strand2_field = strand2_field

        if self._file_name.endswith('.gz') or self._file_name.endswith('gzip'):
            self._open_file = gzip.open
        else:
            self._open_file = open

        if not os.path.exists(valid_pairs_file):
            raise ValueError("File {} does not exist!".format(valid_pairs_file))

        # check that this file is valid:
        with self._open_file(valid_pairs_file, 'rt') as f:
            for line in f:
                line = line.rstrip()
                if line.startswith('#') or line == '':
                    continue

                fields = line.split(sep)

                # find max field
                max_field_ix = 0
                for field_number in {chr1_field, chr2_field,
                                     pos1_field, pos2_field,
                                     strand1_field, strand2_field}:
                    if field_number is not None:
                        max_field_ix = max(max_field_ix, field_number)

                if len(fields) < max_field_ix + 1:
                    raise ValueError("Not enough fields ({}) in file {}".format(len(fields),
                                                                                valid_pairs_file))

                # ensure we can transform pos fields to int
                int(fields[pos1_field])
                int(fields[pos2_field])
                # ensure strand fields are valid if specified
                if strand1_field is not None and fields[strand1_field] not in {'+', '-', '.', '-1', '1', '+1'}:
                    raise ValueError("Cannot read strand1 field!")
                if strand2_field is not None and fields[strand2_field] not in {'+', '-', '.', '-1', '1', '+1'}:
                    raise ValueError("Cannot read strand2 field!")

                break

    def _iter_read_pairs(self, *args, **kwargs):
        """
        Iterate over read pairs encoded in each line of the txt file.

        :return: read pair iterator over tuples of the form
                 (:class:`~MinimalRead`, :class:`~MinimalRead`)
        """
        with self._open_file(self._file_name, 'rt') as f:
            for line in f:
                line = line.rstrip()
                if line == '' or line.startswith('#'):
                    continue
                fields = line.split(self.sep)

                strand1 = fields[self.strand1_field] if self.strand1_field is not None else '+'
                strand2 = fields[self.strand2_field] if self.strand2_field is not None else '+'

                read1 = MinimalRead(chromosome=fields[self.chr1_field],
                                    position=int(fields[self.pos1_field]),
                                    strand=strand1)
                read2 = MinimalRead(chromosome=fields[self.chr2_field],
                                    position=int(fields[self.pos2_field]),
                                    strand=strand2)
                yield (read1, read2)


class HicProPairGenerator(TxtReadPairGenerator):
    """
    Read pair generator for HiC-Pro "validPairs" files.

    This generator is a subclass of :class:`~TxtReadPairGenerator`
    with presets for fields in HiC-Pro validPairs files.
    """
    def __init__(self, file_name):
        """
        Inititalise this HiC-Pro read pair generator.

        :param file_name: Path to HiC-Pro ".validPairs" file
        """
        TxtReadPairGenerator.__init__(self, file_name, sep="\t",
                                      chr1_field=1, pos1_field=2, strand1_field=3,
                                      chr2_field=4, pos2_field=5, strand2_field=6)


class FourDNucleomePairGenerator(TxtReadPairGenerator):
    """
    Read pair generator that works on 4D Nucleome ".pairs" files.

    For details on the 4D Nucleome pairs format see:
    https://github.com/4dn-dcic/pairix/blob/master/pairs_format_specification.md

    """
    def __init__(self, pairs_file):
        if pairs_file.endswith('.gz') or pairs_file.endswith('gzip'):
            open_file = gzip.open
        else:
            open_file = open

        columns = dict()
        with open_file(pairs_file, 'rt') as f:
            for line_ix, line in enumerate(f):
                if line_ix == 0 and not line.startswith("## pairs format"):
                    raise ValueError("Not a 4D nucleome pairs format file."
                                     "Missing '## pairs format X.X' header line.")

                line = line.rstrip()
                if not line.startswith('#'):
                    raise ValueError("Pairs file does not contain a "
                                     "'#columns' entry in the header")

                if line.startswith('#columns:'):
                    _, columns_field = line.split(':')
                    for i, name in columns_field.split():
                        columns[name] = i

        TxtReadPairGenerator.__init__(self, pairs_file, sep=None,
                                      chr1_field=columns['chr1'],
                                      pos1_field=columns['pos1'],
                                      strand1_field=columns['strand1'] if 'strand1' in columns else None,
                                      chr2_field=columns['chr2'],
                                      pos2_field=columns['pos2'],
                                      strand2_field=columns['strand2'] if 'strand2' in columns else None,
                                      )


class SamBamReadPairGenerator(ReadPairGenerator):
    """
    Generate read pairs from paired-end SAM/BAM files.

    This :class:`~ReadPairGenerator` iterates over two SAM or BAM
    files that have been sorted by qname (for example with
    :code:`samtools sort -n`).

    Chimeric reads (mapping partially to multiple genomic locations),
    such as output by BWA, are handled as follows: If both mate pairs
    are chimeric, they are removed. If only one mate is chimeric, but
    it is split into more than 2 alignments, it is also removed. If
    one mate is chimeric and split into two alignments. If one part
    of the chimeric alignment maps within 100bp of the regular alignment,
    the read pair is kept and returned. In all other cases, the pair is
    removed.
    """
    def __init__(self, sam_file1, sam_file2, check_sorted=True):
        ReadPairGenerator.__init__(self)
        self.sam_file1 = sam_file1
        self.sam_file2 = sam_file2
        self._check_sorted = check_sorted
        if not os.path.exists(self.sam_file1):
            raise ValueError("File {} does not exist!".format(self.sam_file1))
        if not os.path.exists(self.sam_file2):
            raise ValueError("File {} does not exist!".format(self.sam_file2))

    def _iter_read_pairs(self, *args, **kwargs):
        max_dist_same_locus = kwargs.get('max_dist_same_locus', 100)
        logger.info("Starting to generate read pairs from SAM")

        def _all_reads(iterator, last_read=None):
            reads = []
            if last_read is not None:
                if last_read.is_unmapped:
                    self._filter_stats[self._unmapped_filter_ix] += 1
                else:
                    reads.append(last_read)

            next_read = None
            try:
                next_read = next(iterator)
                while len(reads) == 0 or natural_cmp(next_read.qname.encode(), reads[0].qname.encode()) == 0:
                    if not next_read.is_unmapped:
                        reads.append(next_read)
                    else:
                        self._filter_stats[self._unmapped_filter_ix] += 1
                    next_read = next(iterator)
            except StopIteration:
                if len(reads) == 0:
                    raise
            return reads[0].qname.encode(), reads, next_read

        def _find_pair(reads1, reads2):
            """
            :return: read1, read2, is_chimeric
            """
            if len(reads1) == len(reads2) == 1:
                return reads1[0], reads2[0], False
            elif (len(reads1) == 1 and len(reads2) == 2) or (len(reads2) == 1 and len(reads1) == 2):
                if len(reads2) > len(reads1):
                    reads1, reads2 = reads2, reads1

                read2 = reads2[0]
                match_pos2 = find_alignment_match_positions(read2, longest=True)[0]
                if match_pos2 is None:
                    return None, None, True

                read1 = None
                same_locus = False
                for read in reads1:
                    if read.reference_id != read2.reference_id:
                        read1 = read
                    else:
                        match_pos1 = find_alignment_match_positions(read, longest=True)[0]
                        if match_pos1 is None:
                            return None, None, True

                        if min(abs(match_pos1[0] - match_pos2[1]),
                               abs(match_pos1[1] - match_pos2[0])) > max_dist_same_locus:
                            read1 = read
                        else:
                            same_locus = True

                if same_locus:
                    return read1, read2, True
            return None, None, False

        with pysam.AlignmentFile(self.sam_file1) as sam1:
            with pysam.AlignmentFile(self.sam_file2) as sam2:
                normal_pairs = 0
                chimeric_pairs = 0
                abnormal_pairs = 0

                sam1_iter = iter(sam1)
                sam2_iter = iter(sam2)
                try:
                    qname1, reads1, next_read1 = _all_reads(sam1_iter)
                    qname2, reads2, next_read2 = _all_reads(sam2_iter)
                    while True:
                        check1 = False
                        check2 = False
                        previous_qname1 = qname1
                        previous_qname2 = qname2

                        cmp = natural_cmp(qname1, qname2)
                        if cmp == 0:  # read name identical
                            read1, read2, is_chimeric = _find_pair(reads1, reads2)
                            if read1 is not None and read2 is not None:
                                yield (read1, read2)
                                if is_chimeric:
                                    chimeric_pairs += 1
                                else:
                                    normal_pairs += 1
                            else:
                                abnormal_pairs += 1
                            qname1, reads1, next_read1 = _all_reads(sam1_iter, last_read=next_read1)
                            qname2, reads2, next_read2 = _all_reads(sam2_iter, last_read=next_read2)
                            check1, check2 = True, True
                        elif cmp < 0:  # first pointer behind
                            qname1, reads1, next_read1 = _all_reads(sam1_iter, last_read=next_read1)
                            check1 = True
                        else:  # second pointer behind
                            qname2, reads2, next_read2 = _all_reads(sam2_iter, last_read=next_read2)
                            check2 = True

                        # check that the files are sorted
                        if self._check_sorted:
                            if check1 and natural_cmp(previous_qname1, qname1) > 0:
                                raise ValueError("First SAM file is not sorted by "
                                                 "read name (samtools sort -n)! Read names:"
                                                 "{} and {}".format(previous_qname1, qname1))
                            if check2 and natural_cmp(previous_qname2, qname2) > 0:
                                raise ValueError("Second SAM file is not sorted by "
                                                 "read name (samtools sort -n)! Read names:"
                                                 "{} and {}".format(previous_qname2, qname2))
                except StopIteration:
                    logger.info("Done generating read pairs.")
                    logger.info("Normal pairs: {}".format(normal_pairs))
                    logger.info("Chimeric pairs: {}".format(chimeric_pairs))
                    logger.info("Abnormal pairs: {}".format(abnormal_pairs))

    def filter_quality(self, cutoff=30):
        """
        Convenience function that registers a QualityFilter.
        The actual algorithm and rationale used for filtering will depend on the
        internal _mapper attribute.

        :param cutoff: Minimum mapping quality (mapq) a read must have to pass
                       the filter
        """
        mask = Mask('map quality', 'Mask read pairs with a mapping quality lower than {}'.format(cutoff),
                    ix=len(self.filters))
        quality_filter = QualityFilter(cutoff, mask)
        self.add_filter(quality_filter)

    def filter_unmapped(self):
        """
        Convenience function that registers an UnmappedFilter.
        """
        mask = Mask('unmappable', 'Mask read pairs that are unmapped', ix=len(self.filters))
        unmapped_filter = UnmappedFilter(mask)
        self.add_filter(unmapped_filter)

    def filter_multi_mapping(self, strict=True):
        """
        Convenience function that registers a UniquenessFilter.
        The actual algorithm and rationale used for filtering will depend on the
        internal _mapper attribute.

        :param strict: If True will filter if XS tag is present. If False,
                       will filter only when XS tag is not 0. This is applied if
                       alignments are from bowtie2.
        """
        mask = Mask('multi-mapping', 'Mask reads that do not map uniquely (according to XS tag)',
                    ix=len(self.filters))
        uniqueness_filter = UniquenessFilter(strict, mask)
        self.add_filter(uniqueness_filter)


class FragmentRead(object):
    """
    Class representing a fragment-mapped read.

    .. attribute:: fragment

        A :class:`~kaic.GenomicRegion` delineated by
        restriction sites.

    .. attribute:: position

        The position of this read in base-pairs (1-based) from the
        start of the chromosome it maps to.

    .. attribute:: strand

        The strand this read maps to (-1 or +1).

    .. attribute:: qname_ix

        Index of the read name, so we don't have to store the
        exact name on disk
    """

    def __init__(self, fragment=None, position=None, strand=0, qname_ix=None):
        """
        Initialize this :class:`~FragmentRead` object.

        :param fragment: A :class:`~kaic.GenomicRegion` delineated by
                         restriction sites.
        :param position: The position of this read in base-pairs (1-based) from the
                         start of the chromosome it maps to.
        :param strand: The strand this read maps to (-1 or +1).
        """
        self.fragment = fragment
        self.position = position
        self.strand = strand
        self.qname_ix = qname_ix

    def re_distance(self):
        """
        Get the distance of the alignment to the nearest restriction site.
        :return: int
        """
        return min(abs(self.position - self.fragment.start),
                   abs(self.position - self.fragment.end))

    def __repr__(self):
        return "%s: %d-(%d[%d])-%d" % (self.fragment.chromosome,
                                       self.fragment.start,
                                       self.position,
                                       self.strand,
                                       self.fragment.end)


class LazyFragmentRead(FragmentRead):
    """
    :class:`~FragmentRead` implementation with lazy attribute loading.

    .. attribute:: fragment

        A :class:`~kaic.GenomicRegion` delineated by
        restriction sites.

    .. attribute:: position

        The position of this read in base-pairs (1-based) from the
        start of the chromosome it maps to.

    .. attribute:: strand

        The strand this read maps to (-1 or +1).

    .. attribute:: qname_ix

        Index of the read name, so we don't have to store the
        exact name on disk
    """
    def __init__(self, row, pairs, side="left"):
        self._row = row
        self._pairs = pairs
        self._side = side

    @property
    def position(self):
        return self._row[self._side + "_read_position"]

    @property
    def strand(self):
        return self._row[self._side + "_read_strand"]

    @property
    def qname_ix(self):
        return self._row[self._side + "_read_qname_ix"]

    @property
    def fragment(self):
        return LazyFragment(self._row, self._pairs, side=self._side)


class LazyFragment(GenomicRegion):
    """
    :class:`~kaic.GenomicRegion` representing a fragment with lazy attribute loading.

    .. attribute:: chromosome

        The reference sequence this region is located on

    .. attribute:: start

        start position of this region on the reference (1-based, inclusive)

    .. attribute:: end

        end position of this region on the reference (1-based, inclusive)

    .. attribute:: strand

        strand of the reference this region is located on (-1, 1, 0, or None)

    .. attribute:: ix

        Region index within the context of regions from the same object.
    """
    def __init__(self, row, pairs, ix=None, side="left"):
        self._row = row
        self._pairs = pairs
        self._side = side
        self._static_ix = ix

    @property
    def chromosome(self):
        return self._pairs._ix_to_chromosome[self._row[self._side + "_fragment_chromosome"]]

    @property
    def start(self):
        return self._row[self._side + "_fragment_start"]

    @property
    def end(self):
        return self._row[self._side + "_fragment_end"]

    @property
    def strand(self):
        return 1

    @property
    def ix(self):
        if self._static_ix is None:
            return self._row['source'] if self._side == 'left' else self._row['sink']
        return self._static_ix


class ReadPairs(RegionPairsTable):
    """
    Class representing a collection of read pairs mapped to restriction fragments.

    This class is a :class:`~kaic.RegionBased` object, where each
    :class:`~kaic.GenomicRegion` represents a restriction fragment
    from a Hi-C experiment. A list of fragments can be obtained with
    the :func:`~kaic.regions.genome_regions` function, for example.

    To create a :class:`~ReadPairs` object, you first have to add
    the restriction fragments before adding read pairs:

    .. code::

        import kaic

        re_fragments = kaic.genome_regions("hg19_chr18_19.fa", "HindIII")

        rp = kaic.ReadPairs()
        rp.add_regions(re_fragments.regions)

    Read pairs can easily be generate fro different types of input using
    :class:`~ReadPairGenerator` implementations, e.g.
    :class:`~HicProReadPairGenerator` or :class:`~SamBamReadPairGenerator`.

    .. code::

        rp_generator = kaic.SamBamReadPairGenerator("output/sam/SRR4271982_chr18_19_1_sort.bam",
                                                    "output/sam/SRR4271982_chr18_19_2_sort.bam")
        rp.add_read_pairs(rp_generator, threads=4)


    You can query regions using the :class:`~kaic.RegionBased` interface:

    .. code::

        chr1_fragments = rp.regions("chr1")

    and you can iterate over read pairs using the :func:`~ReadPairs.pairs`:

    .. code:

        for pair in rp.pairs(lazy=True):
            print(pair)

    you can also use the :cls:Region
    """
    _classid = 'READPAIRS'

    def __init__(self, file_name=None, mode='a',
                 _group_name='fragment_map',
                 _table_name_fragments='fragments',
                 _table_name_pairs='pairs',
                 tmpdir=None):
        """
        Initialize empty FragmentMappedReadPairs object.

        :param file_name: Path to a file that will be created to save
                          this object or path to an existing HDF5 file
                          representing a FragmentMappedReadPairs object.
        :param mode: File mode. Defaults to 'a' (append). Use 'w' to overwrite
                     an existing file in the same location, and 'r' for safe
                     read-only access.
        """
        RegionPairsTable.__init__(self, file_name=file_name, mode=mode, tmpdir=tmpdir,
                                  additional_edge_fields={
                                      'ix': t.Int32Col(pos=0),
                                      'left_read_position': t.Int64Col(pos=1),
                                      'left_read_strand': t.Int8Col(pos=2),
                                      'left_fragment_start': t.Int64Col(pos=3),
                                      'left_fragment_end': t.Int64Col(pos=4),
                                      'left_fragment_chromosome': t.Int32Col(pos=5),
                                      'right_read_position': t.Int64Col(pos=6),
                                      'right_read_strand': t.Int8Col(pos=7),
                                      'right_fragment_start': t.Int64Col(pos=8),
                                      'right_fragment_end': t.Int64Col(pos=9),
                                      'right_fragment_chromosome': t.Int32Col(pos=10)
                                  },
                                  _table_name_regions=_table_name_fragments,
                                  _table_name_edges=_table_name_pairs)

        self._pairs = self._edges
        if self._partition_breaks is None:
            self._pair_count = 0
        else:
            self._pair_count = sum(edge_table._original_len()
                                   for _, edge_table in self._iter_edge_tables())
        self._ix_to_chromosome = dict()
        self._chromosome_to_ix = dict()
        self._update_references()

    def _update_references(self):
        """
        Update internal chromosome index dictionaries.
        """
        chromosomes = []
        for region in self.regions(lazy=True):
            if len(chromosomes) == 0 or chromosomes[-1] != region.chromosome:
                chromosomes.append(region.chromosome)

        for i, chromosome in enumerate(chromosomes):
            self._ix_to_chromosome[i] = chromosome
            self._chromosome_to_ix[chromosome] = i

    def _flush_regions(self):
        """
        Write buffered regions to file and update region references.
        """
        if self._regions_dirty:
            RegionPairsTable._flush_regions(self)
            self._update_references()

    def flush(self, silent=config.hide_progressbars):
        """
        Write buffered data to file and update indexes,

        :param silent: If True, does not use progressbars.
        """
        RegionPairsTable.flush(self, silent=silent)

    def _read_fragment_info(self, read):
        chromosome = read.reference_name
        fragment_info = None
        for row in self._regions.where(
                        "(start <= %d) & (end >= %d) & (chromosome == b'%s')" % (read.pos, read.pos, chromosome)):
            fragment_info = [row['ix'], self._chromosome_to_ix[chromosome], row['start'], row['end']]

        if fragment_info is None:
            raise ValueError("No matching region can be found for {}".format(read))

        return fragment_info

    def _read_pair_fragment_info(self, read_pair):
        read1, read2 = read_pair
        f_ix1, f_chromosome_ix1, f_start1, f_end1 = self._read_fragment_info(read1)
        f_ix2, f_chromosome_ix2, f_start2, f_end2 = self._read_fragment_info(read2)
        r_strand1 = -1 if read1.flag & 16 else 1
        r_strand2 = -1 if read2.flag & 16 else 1
        return ((read1.pos, r_strand1, f_ix1, f_chromosome_ix1, f_start1, f_end1),
                (read2.pos, r_strand2, f_ix2, f_chromosome_ix2, f_start2, f_end2))

    def _read_pairs_fragment_info(self, read_pairs, threads=4, batch_size=1000000, timeout=600):
        """
        Parallel loading of read pairs along with mapping to restriction fragments.

        :param read_pairs: iterator of read pairs, typically
                           from a :class:`~ReadPairGenerator`
        :param threads: Number of threads used for parallel
                        fragment info finding.
        :param batch_size: Number of read pairs sent to each worker
        :param timeout: Time to wait for reply of first worker. If this
                        threshold is exceeded before any read pairs have been
                        returned, a warning is displayed.
        """
        fragment_infos = defaultdict(list)
        fragment_ends = defaultdict(list)
        for region in self.regions(lazy=True):
            chromosome = region.chromosome
            fragment_infos[chromosome].append((region.ix, self._chromosome_to_ix[chromosome],
                                               region.start, region.end))
            fragment_ends[chromosome].append(region.end)

        worker_pool = None
        t_pairs = None
        try:
            monitor = Monitor()
            input_queue = mp.Queue(maxsize=2*threads)
            output_queue = mp.Queue(maxsize=2*threads)

            monitor.set_generating_pairs(True)
            t_pairs = threading.Thread(target=_read_pairs_worker, args=(read_pairs, input_queue,
                                                                        monitor, batch_size))
            t_pairs.daemon = True
            t_pairs.start()

            worker_pool = mp.Pool(threads, _fragment_info_worker,
                                  (monitor, input_queue, output_queue, fragment_infos, fragment_ends))

            output_counter = 0
            while output_counter < monitor.value() or not monitor.workers_idle() or monitor.is_generating_pairs():
                try:
                    read_pair_infos = output_queue.get(block=True, timeout=timeout)

                    for read1_info, read2_info in msgpack.loads(read_pair_infos):
                        yield read1_info, read2_info
                    output_counter += 1
                    del read_pair_infos
                except Empty:
                    logger.warning("Reached SAM pair generator timeout. This could mean that no "
                                   "valid read pairs were found after filtering. "
                                   "Check filter settings!")
        finally:
            if worker_pool is not None:
                worker_pool.terminate()
            if t_pairs is not None:
                t_pairs.join()

    def _add_infos(self, fi1, fi2):
        r_pos1, r_strand1, f_ix1, f_chromosome_ix1, f_start1, f_end1 = fi1
        r_pos2, r_strand2, f_ix2, f_chromosome_ix2, f_start2, f_end2 = fi2

        edge = Edge(ix=self._pair_count,
                    source=f_ix1, sink=f_ix2,
                    left_read_position=r_pos1, right_read_position=r_pos2,
                    left_read_strand=r_strand1, right_read_strand=r_strand2,
                    left_fragment_start=f_start1, right_fragment_start=f_start2,
                    left_fragment_end=f_end1, right_fragment_end=f_end2,
                    left_fragment_chromosome=f_chromosome_ix1,
                    right_fragment_chromosome=f_chromosome_ix2)

        self._add_pair(edge)

    def _fast_add_infos(self, fi1, fi2, default_edge):
        if fi1[2] > fi2[2]:
            r_pos1, r_strand1, f_ix1, f_chromosome_ix1, f_start1, f_end1 = fi2
            r_pos2, r_strand2, f_ix2, f_chromosome_ix2, f_start2, f_end2 = fi1
        else:
            r_pos1, r_strand1, f_ix1, f_chromosome_ix1, f_start1, f_end1 = fi1
            r_pos2, r_strand2, f_ix2, f_chromosome_ix2, f_start2, f_end2 = fi2

        edge = copy.copy(default_edge)
        edge[self._field_names_dict['ix']] = self._pair_count
        edge[self._field_names_dict['source']] = f_ix1
        edge[self._field_names_dict['sink']] = f_ix2
        edge[self._field_names_dict['left_read_position']] = r_pos1
        edge[self._field_names_dict['right_read_position']] = r_pos2
        edge[self._field_names_dict['left_read_strand']] = r_strand1
        edge[self._field_names_dict['right_read_strand']] = r_strand2
        edge[self._field_names_dict['left_fragment_start']] = f_start1
        edge[self._field_names_dict['right_fragment_start']] = f_start2
        edge[self._field_names_dict['left_fragment_end']] = f_end1
        edge[self._field_names_dict['right_fragment_end']] = f_end2
        edge[self._field_names_dict['left_fragment_chromosome']] = f_chromosome_ix1
        edge[self._field_names_dict['right_fragment_chromosome']] = f_chromosome_ix2

        self._add_edge_from_tuple(tuple(edge))

        self._pair_count += 1

    def _default_edge_list(self):
        record = [None] * len(self._field_names_dict)
        for name, ix in self._field_names_dict.items():
            record[ix] = self._edge_field_defaults[name]
        return record

    def add_read_pair(self, read_pair, flush=True):
        fi1, fi2 = self._read_pair_fragment_info(read_pair)
        self._add_infos(fi1, fi2)
        if flush:
            self.flush()

    def _flush_fragment_info_buffer(self):
        for (source_partition, sink_partition), edges in self._edge_buffer.items():
            edge_table = self._edge_table(source_partition, sink_partition)
            row = edge_table.row

            for fi1, fi2 in edges:
                if fi1[2] > fi2[2]:
                    fi1, fi2 = fi2, fi1

                row['ix'] = self._pair_count
                row['source'] = fi1[2]
                row['sink'] = fi2[2]
                row['left_read_position'] = fi1[0]
                row['right_read_position'] = fi2[0]
                row['left_read_strand'] = fi1[1]
                row['right_read_strand'] = fi2[1]
                row['left_fragment_start'] = fi1[4]
                row['right_fragment_start'] = fi2[4]
                row['left_fragment_end'] = fi1[5]
                row['right_fragment_end'] = fi2[5]
                row['left_fragment_chromosome'] = fi1[3]
                row['right_fragment_chromosome'] = fi2[3]
                row.append()
                self._pair_count += 1

            edge_table.flush(update_index=False)
        self._edge_buffer = defaultdict(list)

    def add_read_pairs(self, read_pairs, batch_size=1000000, threads=1):
        """
        Add read pairs to this object.

        This function requires tuples of read pairs as input, for
        example from :class:`~MinimalRead` or :class:`~pysam.AlignedSegment`.
        Typically, you won't have to construct these from scratch, but can
        use one of the :class:`~ReadPairGenerator` classes to generate
        read pairs from input files.

        .. code::

            import kaic

            re_fragments = kaic.genome_regions("hg19_chr18_19.fa", "HindIII")
            rp = kaic.ReadPairs()
            rp.add_regions(re_fragments.regions)

            # read pairs are added here from BAM files
            rp_generator = kaic.SamBamReadPairGenerator("output/sam/SRR4271982_chr18_19_1_sort.bam",
                                                        "output/sam/SRR4271982_chr18_19_2_sort.bam")
            rp.add_read_pairs(rp_generator, threads=4)

        :param read_pairs: iterator over tuples of read pairs. Typically
                           instances of :class:`~ReadPairGenerator`
        :param batch_size: Batch size of read pairs sent to fragment info workers
        :param threads: Number of threads for simultaneous fragment info finding
        """
        self._edges_dirty = True
        self._disable_edge_indexes()

        start_time = timer()
        chunk_start_time = timer()
        pairs_counter = 0
        for fi1, fi2 in self._read_pairs_fragment_info(read_pairs, batch_size=batch_size, threads=threads):
            source_partition, sink_partition = self._get_edge_table_tuple(fi1[2], fi2[2])
            self._edge_buffer[(source_partition, sink_partition)].append([fi1, fi2])

            pairs_counter += 1
            if pairs_counter % self._edge_buffer_size == 0:
                self._flush_fragment_info_buffer()
                end_time = timer()
                logger.debug("Wrote {} pairs in {}s (current {} chunk: {}s)".format(
                    pairs_counter, end_time - start_time,
                    self._edge_buffer_size,
                    end_time - chunk_start_time
                ))
                chunk_start_time = timer()
        self._flush_fragment_info_buffer()
        end_time = timer()
        logger.debug("Wrote {} pairs in {}s".format(
            pairs_counter, end_time - start_time
        ))

        logger.info('Done saving read pairs.')

        if isinstance(read_pairs, ReadPairGenerator):
            stats = read_pairs.stats()
            if 'read_filter_stats' not in self.meta:
                self.meta.read_filter_stats = stats
            else:
                self.meta.read_filter_stats = add_dict(self.meta.read_filter_stats, stats)

        self.flush()
        logger.info("Done adding pairs.")

    def _add_pair(self, pair):
        self.add_edge(pair, check_nodes_exist=False, replace=True)
        self._pair_count += 1

    def _pair_from_row(self, row, lazy_pair=None):
        """
        Convert a PyTables row to a FragmentReadPair
        """
        if lazy_pair is not None:
            lazy_pair.left._row = row
            lazy_pair.right._row = row
            lazy_pair.ix = row['ix']
            return lazy_pair
        else:
            fragment1 = GenomicRegion(start=row['left_fragment_start'],
                                      end=row['left_fragment_end'],
                                      chromosome=self._ix_to_chromosome[row['left_fragment_chromosome']],
                                      ix=row['source'])
            fragment2 = GenomicRegion(start=row['right_fragment_start'],
                                      end=row['right_fragment_end'],
                                      chromosome=self._ix_to_chromosome[row['right_fragment_chromosome']],
                                      ix=row['sink'])

            left_read = FragmentRead(fragment1, position=row['left_read_position'],
                                     strand=row['left_read_strand'])
            right_read = FragmentRead(fragment2, position=row['right_read_position'],
                                      strand=row['right_read_strand'])

            return FragmentReadPair(left_read=left_read, right_read=right_read, ix=row['ix'])

    def get_ligation_structure_biases(self, sampling=None, skip_self_ligations=True,
                                      **kwargs):

        """
        Compute the ligation biases (inward and outward to same-strand) of this data set.

        :param sampling: Approximate number of data points to average per point
                         in the plot. If None (default), this will
                         be determined on a best-guess basis.
        :param skip_self_ligations: If True (default), will not consider
                                    self-ligated fragments for assessing
                                    the error rates.
        :param unfiltered: If True, uses all read pairs, even those that do not
                           pass filters, for the ligation error calculation
        :return: tuple with (list of gap sizes between reads, list of matching le type ratios)
        """
        n_pairs = len(self)
        type_same = 0
        type_inward = 1
        type_outward = 2

        def _init_gaps_and_types():
            same_count = 0
            inward_count = 0
            outward_count = 0
            same_fragment_count = 0
            inter_chrm_count = 0
            gaps = []
            types = []

            with RareUpdateProgressBar(max_value=len(self), silent=config.hide_progressbars,
                                       prefix="Ligation error") as pb:
                for i, pair in enumerate(self.pairs(lazy=True, **kwargs)):
                    if pair.is_same_fragment():
                        same_fragment_count += 1
                        if skip_self_ligations:
                            continue
                    if pair.is_same_chromosome():
                        gap_size = pair.get_gap_size()
                        if gap_size > 0:
                            gaps.append(gap_size)
                            if pair.is_outward_pair():
                                types.append(type_outward)
                                outward_count += 1
                            elif pair.is_inward_pair():
                                types.append(type_inward)
                                inward_count += 1
                            else:
                                types.append(type_same)
                                same_count += 1
                    else:
                        inter_chrm_count += 1
                    pb.update(i)

            logger.info("Pairs: %d" % n_pairs)
            logger.info("Inter-chromosomal: {}".format(inter_chrm_count))
            logger.info("Same fragment: {}".format(same_fragment_count))
            logger.info("Same: {}".format(same_count))
            logger.info("Inward: {}".format(inward_count))
            logger.info("Outward: {}".format(outward_count))
            return gaps, types

        def _sort_data(gaps, types):
            points = zip(gaps, types)
            sorted_points = sorted(points)
            return zip(*sorted_points)

        def _calculate_ratios(gaps, types, sampling):
            x = []
            inward_ratios = []
            outward_ratios = []
            bin_sizes = []
            counter = 0
            same_counter = 0
            mids = 0
            outwards = 0
            inwards = 0
            for typ, gap in zip(types, gaps):
                mids += gap
                if typ == type_same:
                    same_counter += 1
                elif typ == type_inward:
                    inwards += 1
                else:
                    outwards += 1
                counter += 1
                if same_counter > sampling:
                    x.append(int(mids / counter))
                    inward_ratios.append(inwards / same_counter)
                    outward_ratios.append(outwards / same_counter)
                    bin_sizes.append(counter)
                    same_counter = 0
                    counter = 0
                    mids = 0
                    outwards = 0
                    inwards = 0
            return list(map(np.array, [x, inward_ratios, outward_ratios, bin_sizes]))

        gaps, types = _init_gaps_and_types()
        # sort data
        gaps, types = _sort_data(gaps, types)
        # best guess for number of data points
        sampling = max(100, int(n_pairs * 0.0025)) if sampling is None else sampling
        logger.debug("Number of data points averaged per point in plot: {}".format(sampling))
        # calculate ratios
        return _calculate_ratios(gaps, types, sampling)

    @staticmethod
    def _auto_dist(dists, ratios, sample_sizes, p=0.05, expected_ratio=0.5):
        """
        Function that attempts to infer sane distances for filtering inward
        and outward read pairs.

        Use with caution, it is almost always preferable to plot the ligation
        error and choose the cutoff manually.

        :param dists: List of distances in bp.
        :param ratios: List of ratios
        """

        def x_prop(p_obs, p_exp, n):
            obs = p_obs * n
            exp = p_exp * n
            p = (obs + exp) / (n * 2)
            return abs((p_exp - p_obs) / np.sqrt(p * (1 - p) * (2 / n)))

        ratios = np.clip(ratios, 0.0, 1.0)
        z_scores = np.array([x_prop(r, expected_ratio, b) for r, b in zip(ratios, sample_sizes)])
        which_valid = z_scores < 1.96
        which_valid_indices = np.argwhere(which_valid).flatten()
        if len(which_valid_indices) > 0:
            return int(dists[which_valid_indices[0]])
        return None

    def filter(self, pair_filter, queue=False, log_progress=not config.hide_progressbars):
        """
        Apply a :class:`~FragmentReadPairFilter` to the read pairs in this object.

        :param pair_filter: :class:`~FragmentReadPairFilter`
        :param queue: If True, does not do the filtering immediately, but
                      queues this filter. All queued filters can then be run
                      at the same time using :func:`~ReadPairs.run_queued_filters`
        :param log_progress:
        :return:
        """
        pair_filter.set_pairs_object(self)

        total = 0
        filtered = 0
        if not queue:
            with RareUpdateProgressBar(max_value=sum(1 for _ in self._edges),
                                       silent=not log_progress) as pb:
                for i, (_, edge_table) in enumerate(self._iter_edge_tables()):
                    stats = edge_table.filter(pair_filter, _logging=False)
                    for key, value in stats.items():
                        if key != 0:
                            filtered += stats[key]
                        total += stats[key]
                    pb.update(i)
            if log_progress:
                logger.info("Total: {}. Valid: {}".format(total, total - filtered))
        else:
            self._queued_filters.append(pair_filter)

    def run_queued_filters(self, log_progress=not config.hide_progressbars):
        """
        Run queued filters. See :func:`~ReadPairs.filter`

        :param log_progress: If true, process iterating through all edges
                             will be continuously reported.
        """
        total = 0
        filtered = 0
        with RareUpdateProgressBar(max_value=sum(1 for _ in self._edges),
                                   silent=not log_progress) as pb:
            for i, (_, edge_table) in enumerate(self._iter_edge_tables()):
                for f in self._queued_filters:
                    edge_table.queue_filter(f)

                stats = edge_table.run_queued_filters(_logging=False)
                for key, value in stats.items():
                    if key != 0:
                        filtered += stats[key]
                    total += stats[key]
                pb.update(i)
        if log_progress:
            logger.info("Total: {}. Valid: {}".format(total, total - filtered))

        self._queued_filters = []
        self._update_mappability()

    def filter_pcr_duplicates(self, threshold=3, queue=False):
        """
        Convenience function that applies an :class:`~PCRDuplicateFilter`.

        :param threshold: If distance between two alignments is smaller or
                          equal the threshold, the alignments
                          are considered to be starting at the same position
        :param queue: If True, filter will be queued and can be executed
                      along with other queued filters using
                      run_queued_filters
        """
        mask = self.add_mask_description('PCR duplicates', 'Mask read pairs that are '
                                                           'considered PCR duplicates')
        pcr_duplicate_filter = PCRDuplicateFilter(pairs=self, threshold=threshold, mask=mask)
        self.filter(pcr_duplicate_filter, queue)

    def filter_inward(self, minimum_distance=None, queue=False, **kwargs):
        """
        Convenience function that applies an :class:`~InwardPairsFilter`.

        :param minimum_distance: Minimum distance inward-facing read
                                 pairs must have to pass this filter
        :param queue: If True, filter will be queued and can be executed
                      along with other queued filters using
                      run_queued_filters
        :param kwargs: Additional arguments to pass
                       to :func:`~ReadPairs.get_ligation_structure_biases`
        """
        if minimum_distance is None:
            dists, inward_ratios, _, bins_sizes = self.get_ligation_structure_biases(**kwargs)
            minimum_distance = self._auto_dist(dists, inward_ratios, bins_sizes)
        if minimum_distance:
            mask = self.add_mask_description('inward ligation error',
                                             'Mask read pairs that are inward facing and < {}bp apart'
                                             .format(minimum_distance))
            logger.info("Filtering out inward facing read pairs < {} bp apart".format(minimum_distance))
            inward_filter = InwardPairsFilter(minimum_distance=minimum_distance, mask=mask)
            self.filter(inward_filter, queue)
        else:
            raise Exception('Could not automatically detect a sane distance threshold for '
                            'filtering inward reads')

    def filter_outward(self, minimum_distance=None, queue=False, **kwargs):
        """
        Convenience function that applies an :class:`~OutwardPairsFilter`.

        :param minimum_distance: Minimum distance outward-facing read
                                 pairs must have to pass this filter
        :param queue: If True, filter will be queued and can be executed
                      along with other queued filters using
                      run_queued_filters
        :param kwargs: Additional arguments to pass
                       to :func:`~ReadPairs.get_ligation_structure_biases`
        """
        if minimum_distance is None:
            dists, _, outward_ratios, bins_sizes = self.get_ligation_structure_biases(**kwargs)
            minimum_distance = self._auto_dist(dists, outward_ratios, bins_sizes)
        if minimum_distance:
            mask = self.add_mask_description('outward ligation error',
                                             'Mask read pairs that are outward facing and < {}bp apart'
                                             .format(minimum_distance))
            logger.info("Filtering out outward facing read pairs < {} bp apart".format(minimum_distance))
            outward_filter = OutwardPairsFilter(minimum_distance=minimum_distance, mask=mask)
            self.filter(outward_filter, queue)
        else:
            raise Exception('Could not automatically detect a sane distance threshold for filtering outward reads')

    def filter_ligation_products(self, inward_threshold=None, outward_threshold=None, queue=False, **kwargs):
        """
        Convenience function that applies an :class:`~OutwardPairsFilter` and an :class:`~InwardPairsFilter`.

        :param inward_threshold: Minimum distance inward-facing read
                                 pairs must have to pass this filter.
                                 If None, will be inferred from the data
        :param outward_threshold: Minimum distance outward-facing read
                                 pairs must have to pass this filter.
                                 If None, will be inferred from the data
        :param queue: If True, filter will be queued and can be executed
                      along with other queued filters using
                      run_queued_filters
        :param kwargs: Additional arguments to pass
                       to :func:`~ReadPairs.get_ligation_structure_biases`
        """
        self.filter_inward(inward_threshold, queue=queue, **kwargs)
        self.filter_outward(outward_threshold, queue=queue, **kwargs)

    def filter_re_dist(self, maximum_distance, queue=False):
        """
        Convenience function that applies an :class:`~ReDistanceFilter`.

        :param maximum_distance: Maximum distance a read can have to the
                                 nearest region border (=restriction site)
        :param queue: If True, filter will be queued and can be executed
                      along with other queued filters using
                      run_queued_filters
        """
        mask = self.add_mask_description('restriction site distance',
                                         'Mask read pairs where the cumulative distance of reads to '
                                         'the nearest RE site exceeds {}'.format(maximum_distance))
        re_filter = ReDistanceFilter(maximum_distance=maximum_distance, mask=mask)
        self.filter(re_filter, queue)

    def filter_self_ligated(self, queue=False):
        """
        Convenience function that applies an :class:`~SelfLigationFilter`.

        :param queue: If True, filter will be queued and can be executed
                      along with other queued filters using
                      run_queued_filters
        """
        mask = self.add_mask_description('self-ligations',
                                         'Mask read pairs that represent a self-ligated fragment')
        self_ligation_filter = SelfLigationFilter(mask=mask)
        self.filter(self_ligation_filter, queue)

    def __iter__(self):
        """
        Iterate over unfiltered fragment-mapped read pairs.
        """
        return self.pairs(lazy=False)

    def pairs(self, key=None, lazy=False, *args, **kwargs):
        """
        Iterate over the :class:`~FragmentReadPair` objects.

        :param key: Region string of the form <chromosome>[:<start>-<end>],
                    :class:`~kaic.GenomicRegion` or tuples thereof
        :param lazy: If True, use lazy loading of objects and their attributes.
                     Much faster, but can lead to unexpected results if one is
                     not careful. For example, this: :code:`list(object.pairs())`
                     is not the same as :code:`list(object.pairs(lazy=True))`! In
                     the latter case, all objects in the list will be identical
                     due to the lazy loading process. Only use lazy loading to access
                     attributes in an iterator!
        :param args: Positional arguments passed to :func:`~RegionPairs.edges_dict`
        :param kwargs: Keyword arguments passed to :func:`~RegionPairs.edges_dict`
        :return:
        """
        if lazy:
            fr1 = LazyFragmentRead({}, self, side='left')
            fr2 = LazyFragmentRead({}, self, side='right')
            lazy_pair = FragmentReadPair(fr1, fr2, ix=None)
        else:
            lazy_pair = None
        for row in self.edges_dict(key=key, *args, **kwargs):
            yield self._pair_from_row(row, lazy_pair=lazy_pair)

    def get_edge(self, item, *row_conversion_args, **row_conversion_kwargs):
        """
        Get an edge by index.

        :param row_conversion_args: Arguments passed to :func:`RegionPairs._row_to_edge`
        :param row_conversion_kwargs: Keyword arguments passed to :func:`RegionPairs._row_to_edge`
        :return: :class:`~Edge`
        """
        if item < 0:
            item += len(self)

        l = 0
        for _, edge_table in self._iter_edge_tables():
            if l <= item < l + len(edge_table):
                res = edge_table[item - l]
                return self._row_to_edge(res, *row_conversion_args, **row_conversion_kwargs)
            l += len(edge_table)
        raise IndexError("index out of range (%d)" % item)

    def __getitem__(self, item):
        if isinstance(item, int):
            edge = self.get_edge(item)
            fragment1 = GenomicRegion(start=edge.left_fragment_start,
                                      end=edge.left_fragment_end,
                                      chromosome=self._ix_to_chromosome[edge.left_fragment_chromosome],
                                      ix=edge.source)
            fragment2 = GenomicRegion(start=edge.right_fragment_start,
                                      end=edge.right_fragment_end,
                                      chromosome=self._ix_to_chromosome[edge.right_fragment_chromosome],
                                      ix=edge.sink)

            left_read = FragmentRead(fragment1, position=edge.left_read_position,
                                     strand=edge.left_read_strand)
            right_read = FragmentRead(fragment2, position=edge.right_read_position,
                                      strand=edge.right_read_strand)

            return FragmentReadPair(left_read=left_read, right_read=right_read, ix=edge.ix)
        else:
            pairs = []
            for row in self.edges.get_row_range(item):
                pairs.append(self._pair_from_row(row))
            return pairs

    def __len__(self):
        l = 0
        for _, edge_table in self._iter_edge_tables():
            l += len(edge_table)
        return l

    def to_hic(self, file_name=None, tmpdir=None, _hic_class=Hic):
        """
        Convert this :class:`~ReadPairs` to a :class:`~kaic.Hic` object.

        :param file_name: Path to the :class:`~kaic.Hic` output file
        :param tmpdir: If True (or path to temporary directory) will
                       work in temporary directory until closed
        """
        hic = _hic_class(file_name=file_name, mode='w', tmpdir=tmpdir)
        hic.add_regions(self.regions(), preserve_attributes=False)

        hic._disable_edge_indexes()

        n_pairs = len(self)
        pairs_counter = 0
        with RareUpdateProgressBar(max_value=n_pairs, silent=config.hide_progressbars) as pb:
            for _, pairs_edge_table in self._iter_edge_tables():

                partition_edge_buffer = defaultdict(lambda: defaultdict(int))
                for row in pairs_edge_table:
                    key = (row['source'], row['sink'])
                    source_partition = self._get_partition_ix(key[0])
                    sink_partition = self._get_partition_ix(key[1])
                    partition_edge_buffer[(source_partition, sink_partition)][key] += 1
                    pb.update(pairs_counter)
                    pairs_counter += 1

                for hic_partition_key, edge_buffer in viewitems(partition_edge_buffer):
                    hic_edge_table = hic._edge_table(hic_partition_key[0], hic_partition_key[1])
                    row = hic_edge_table.row

                    for (source, sink), weight in viewitems(edge_buffer):
                        row['source'] = source
                        row['sink'] = sink
                        row[hic._default_score_field] = float(weight)
                        row.append()
                    hic_edge_table.flush(update_index=False)
        hic.flush()

        hic._enable_edge_indexes()

        return hic

    def pairs_by_chromosomes(self, chromosome1, chromosome2, **kwargs):
        """
        Only iterate over read pairs in this combination of chromosomes.
        :param chromosome1: Name of first chromosome
        :param chromosome2: Name of second chromosome
        :param kwargs: Keyword arguments passed to :func:`~ReadPairs.pairs`
        :return:
        """
        return self.pairs(key=(chromosome1, chromosome2), **kwargs)

    def filter_statistics(self):
        """
        Get filtering statistics for this object.
        :return: dict with {filter_type: count, ...}
        """
        try:
            read_stats = self.meta.read_filter_stats
        except AttributeError:
            read_stats = dict()

        pair_stats = self.mask_statistics(self._pairs)
        if 'valid' in pair_stats:
            read_stats['valid'] = pair_stats['valid']
        pair_stats.update(read_stats)
        return pair_stats


class FragmentReadPair(object):
    """
    Container for two paired :class:`~FragmentRead` objects.
    """

    def __init__(self, left_read, right_read, ix=None):
        self.left = left_read
        self.right = right_read
        self.ix = ix

    def is_same_chromosome(self):
        """
        Check if both reads are mapped to the same chromosome.

        :return: True is reads map to the same chromosome, False
                 otherwise
        """
        return self.left.fragment.chromosome == self.right.fragment.chromosome

    def is_inward_pair(self):
        """
        Check if reads form an "inward-facing" pair.

        A pair is considered inward-facing if the left read maps
        to the plus the right read to the minus strand and both
        reads map to the same chromosome.

        :return: True if reads are inward-facing, False otherwise
        """
        if not self.is_same_chromosome():
            return False

        if self.left.strand == 1 and self.right.strand == -1:
            return True
        return False

    def is_outward_pair(self):
        """
        Check if reads form an "outward-facing" pair.

        A pair is considered outward-facing if the left read maps
        to the minus the right read to the plus strand and both
        reads map to the same chromosome.

        :return: True if reads are outward-facing, False otherwise
        """
        if not self.is_same_chromosome():
            return False

        if self.left.strand == -1 and self.right.strand == 1:
            return True
        return False

    def is_same_pair(self):
        """
        Check if reads face in the same direction.

        :return: True if reads are facing in the same direction,
                 False otherwise.
        """
        if not self.is_same_chromosome():
            return False

        if self.left.strand == self.right.strand:
            return True
        return False

    def is_same_fragment(self):
        """
        Check if reads map to the same fragment.

        :return: True if reads map to the same fragment,
                 False otherwise.
        """
        if not self.is_same_chromosome():
            return False

        return self.left.fragment.start == self.right.fragment.start

    def get_gap_size(self):
        """
        Get the gap size in base pairs between the fragments these
        reads map to.

        :return: 0 if reads map to the same fragment or neighboring
                 fragments, the distance between fragments if they
                 are on the same chromosome, None otherwise
        """
        if not self.is_same_chromosome():
            return None

        if self.is_same_fragment():
            return 0

        gap = self.right.fragment.start - self.left.fragment.end

        if gap == 1:  # neighboring fragments
            return 0

        return gap

    def __getitem__(self, key):
        if key == 0:
            return self.left
        if key == 1:
            return self.right
        raise KeyError("Can only access read [0] and read [1], not '{}'".format(key))

    def __repr__(self):
        left_repr = self.left.__repr__()
        right_repr = self.right.__repr__()
        return "{} -- {}".format(left_repr, right_repr)


class ReadFilter(object):
    """
    Abstract class that provides filtering functionality for
    :class:`~MinimalRead`, :class:`~pysam.AlignedSegment` or
    compatible.

    To create a custom :class:`~ReadFilter`, extend this
    class and override the valid_read(self, read) method.
    valid_read should return False for a specific read object
    if the object is supposed to be filtered/masked and True
    otherwise. See :class:`~QualityFilter` for an example.

    Pass a custom filter to the filter method in
    :class:`~ReadPairGenerator` to apply it.
    """

    def __init__(self, mask=None):
        """
        Initialize ReadFilter.

        :param mask: The :class:`~kaic.general.Mask` object that
                     should be used to mask
                     filtered Read objects. If None the default
                     Mask will be used.
        """
        if mask is not None:
            self.mask = mask
        else:
            self.mask = Mask('default', 'Default mask')

    def valid_read(self, read):
        """
        Determine if a read is valid or should be filtered.

        When implementing custom read filters this method must be
        overridden. It should return False for reads that
        are to be filtered and True otherwise.

        Internally, the ReadPairs object will iterate over all Read
        instances to determine their validity on an individual
        basis.

        :param read: A :class:`~Read` object
        :return: True if Read is valid, False otherwise
        """
        raise NotImplementedError("ReadFilters must implement valid_read function")


class QualityFilter(ReadFilter):
    """
    Filter mapped reads based on mapping quality.
    """

    def __init__(self, cutoff=30, mask=None):
        """
        :param cutoff: Lowest mapping quality that is still
                       considered acceptable for a mapped read.
        :param mask: Optional Mask object describing the mask
                     that is applied to filtered reads.
        """
        super(QualityFilter, self).__init__(mask)
        self.cutoff = cutoff

    def valid_read(self, read):
        """
        Check if a read has a mapq >= cutoff.
        """
        return read.mapq >= self.cutoff


class ContaminantFilter(ReadFilter):
    """
    Filter reads that also map to a contaminant genome.
    """

    def __init__(self, contaminant_reads, mask=None):
        """
        :param contaminant_reads: A SAM/BAM file representing a
                                  contaminant
        :param mask: Optional Mask object describing the mask
                     that is applied to filtered reads.
        """
        super(ContaminantFilter, self).__init__(mask)

        self.contaminant_names = set()
        with pysam.AlignmentFile(contaminant_reads) as contaminant:
            for read in contaminant:
                self.contaminant_names.add(read.qname)

    def valid_read(self, read):
        """
        Check if a read also maps to a contaminant
        """
        if read.qname in self.contaminant_names:
            return False
        return True


class BwaMemQualityFilter(ReadFilter):
    """
    Filters :code:`bwa mem` generated alignments
    based on the alignment score (normalized by
    the length of the alignment).
    """
    def __init__(self, cutoff=0.90, mask=None):
        """
        :param cutoff: Ratio of the alignment score to the maximum score
                       possible for an alignment that long
        :param mask: Optional Mask object describing the mask
                     that is applied to filtered reads.
        """
        super(BwaMemQualityFilter, self).__init__(mask)
        self.cutoff = cutoff

    def valid_read(self, read):
        """
        Check if a read has a high alignment score.
        """
        if read.alen:
            return float(read.get_tag('AS')) / read.alen >= self.cutoff
        return False


class UniquenessFilter(ReadFilter):
    """
    Filter reads that do not map uniquely to the reference sequence.
    """

    def __init__(self, strict=True, mask=None):
        """
        :param strict: If True, valid_read checks only for the
                       presence of an XS tag. If False, the value
                       of an XS tag also has to be different from 0.
        :param mask: Optional Mask object describing the mask
                     that is applied to filtered reads.
        """
        self.strict = strict
        super(UniquenessFilter, self).__init__(mask)

    def valid_read(self, read):
        """
        Check if a read has an XS tag.

        If strict is enabled checks if a read has an XS tag.
        If not strict, XS has to be smaller than AS (alignment score)
        for a valid read.
        """
        try:
            tag_xs = read.get_tag('XS')
            if self.strict:
                return False
            else:
                tag_as = read.get_tag('AS')
                if tag_as <= tag_xs:
                    return False
        except KeyError:
            pass
        return True


class BwaMemUniquenessFilter(ReadFilter):
    """
    Filters `bwa mem` generated alignments based on whether they are unique or not.
    The presence of a non-zero XS tag does not mean a read is a multi-mapping one.
    Instead, we make sure that the ratio XS/AS is inferior to a certain threshold.
    """
    def __init__(self, strict=False, mask=None):
        """
        :param strict: If True, valid_read checks only for the
                       presence of an XA tag. If False, the edit
                       distance (NM) of an alternative alignment has to be
                       the same or better as the original NM.
        :param mask: Optional Mask object describing the mask
                     that is applied to filtered reads.
        """
        super(BwaMemUniquenessFilter, self).__init__(mask)
        self.strict = strict

    def valid_read(self, read):
        try:
            xa = read.get_tag('XA')
            if self.strict:
                return False

            try:
                nm = read.get_tag('NM')
            except KeyError:
                return False

            for alt in xa.split(';'):
                if alt == '':
                    continue
                _, _, _, nm_alt = alt.split(',')
                if int(nm_alt) <= nm:
                    return False
        except KeyError:
            pass
        return True


class UnmappedFilter(ReadFilter):
    """
    Filter reads that do not map to the reference sequence.
    """
    def __init__(self, mask=None):
        """
        :param mask: Optional Mask object describing the mask
                     that is applied to filtered reads.
        """
        super(UnmappedFilter, self).__init__(mask)

    def valid_read(self, read):
        """
        Check if the the flag bit 4 is set.
        """
        if read.flag & 4:
            return False
        return True


class FragmentReadPairFilter(with_metaclass(ABCMeta, MaskFilter)):
    """
    Abstract class that provides filtering functionality for the
    :class:`~FragmentReadPair` object.

    Extends MaskFilter and overrides valid(self, read) to make
    :class:`~FragmentReadPair` filtering more "natural".

    To create custom filters for the :class:`~FragmentMappedReadPairs`
    object, extend this
    class and override the :func:`~FragmentMappedReadPairFilter.valid_pair` method.
    valid_pair should return False for a specific :class:`~FragmentReadPair` object
    if the object is supposed to be filtered/masked and True
    otherwise. See :class:`~InwardPairsFilter` for an example.

    Pass a custom filter to the filter method in :class:`~FragmentMappedReadPairs`
    to apply it.
    """

    def __init__(self, mask=None):
        super(FragmentReadPairFilter, self).__init__(mask)
        self.pairs = None
        self._lazy_pair = None

    def set_pairs_object(self, pairs):
        self.pairs = pairs
        fr1 = LazyFragmentRead({}, pairs, side='left')
        fr2 = LazyFragmentRead({}, pairs, side='right')
        self._lazy_pair = FragmentReadPair(fr1, fr2)

    @abstractmethod
    def valid_pair(self, fr_pair):
        pass

    def valid(self, row):
        """
        Map validity check of rows to pairs.
        """
        pair = self.pairs._pair_from_row(row, lazy_pair=self._lazy_pair)
        return self.valid_pair(pair)


class InwardPairsFilter(FragmentReadPairFilter):
    """
    Filter inward-facing read pairs at a distance less
    than a specified cutoff.
    """

    def __init__(self, minimum_distance=10000, mask=None):
        """
        Initialize filter.

        :param minimum_distance: Minimum distance below which
                                 reads are invalidated
        :param mask: Optional Mask object describing the mask
                     that is applied to filtered reads.
        """
        super(InwardPairsFilter, self).__init__(mask=mask)
        self.minimum_distance = minimum_distance

    def valid_pair(self, pair):
        """
        Check if a pair is inward-facing and <minimum_distance> apart.
        """
        if pair.is_inward_pair() and pair.get_gap_size() <= self.minimum_distance:
            return False
        return True


class PCRDuplicateFilter(FragmentReadPairFilter):
    """
    Masks alignments that are suspected to be PCR duplicates.
    In order to be considered duplicates, two pairs need to have identical
    start positions of their respective left alignments AND of their right alignments.
    """

    def __init__(self, pairs, threshold=2, mask=None):
        """
        Initialize filter with filter settings.

        :param pairs: The :class:`~FragmentReadPairs` instance that the filter will be
                      applied to
        :param threshold: If distance between two alignments is smaller or equal the threshold,
                          the alignments are considered to be starting at the same position
        :param mask: Optional Mask object describing the mask
                     that is applied to filtered reads.
        """
        FragmentReadPairFilter.__init__(self, mask=mask)
        self.threshold = threshold
        self.pairs = pairs
        self.duplicates_set = set()
        self.duplicate_stats = defaultdict(int)
        original_len = 0
        for _, edge_table in self.pairs._iter_edge_tables():
            original_len += edge_table._original_len()
            self._mark_duplicates(edge_table)

        n_dups = len(self.duplicates_set)
        percent_dups = 1. * n_dups / original_len
        logger.info("PCR duplicate stats: " +
                    "{} ({:.1%}) of pairs marked as duplicate. ".format(n_dups, percent_dups) +
                    " (multiplicity:occurances) " +
                    " ".join("{}:{}".format(k, v) for k, v in self.duplicate_stats.items()))

    def _mark_duplicates(self, edge_table):
        pairs = [
            (
                row['ix'],
                row['left_fragment_chromosome'], row['right_fragment_chromosome'],
                row['left_read_position'], row['right_read_position']
             ) for row in edge_table._iter_visible_and_masked()
        ]
        pairs = sorted(pairs, key=lambda p: p[3])

        current_positions = {}
        current_duplicates = {}
        for ix, left_chromosome, right_chromosome, left_position, right_position in pairs:
            chromosomes = (left_chromosome, right_chromosome)

            # case 1: no current duplicates
            if current_positions.get(chromosomes) is None:
                current_positions[chromosomes] = (left_position, right_position)
                current_duplicates[chromosomes] = 1
                continue

            # case 2: found duplicate
            if (abs(left_position - current_positions[chromosomes][0]) <= self.threshold and
                    abs(right_position - current_positions[chromosomes][1]) <= self.threshold):
                self.duplicates_set.add(ix)
                current_duplicates[chromosomes] += 1
                continue

            # update statistics
            if current_duplicates[chromosomes] > 1:
                self.duplicate_stats[current_duplicates[chromosomes]] += 1

            current_positions[chromosomes] = (left_position, right_position)
            current_duplicates[chromosomes] = 1

    def valid_pair(self, pair):
        """
        Check if a pair is duplicated.
        """
        if pair.ix in self.duplicates_set:
            return False
        return True


class OutwardPairsFilter(FragmentReadPairFilter):
    """
    Filter outward-facing read pairs at a distance less
    than a specified cutoff.
    """

    def __init__(self, minimum_distance=10000, mask=None):
        """
        Initialize filter with filter settings.

        :param minimum_distance: Minimum distance below which
                                 outward-facing reads are invalidated
        :param mask: Optional Mask object describing the mask
                     that is applied to filtered reads.
        """
        super(OutwardPairsFilter, self).__init__(mask=mask)
        self.minimum_distance = minimum_distance

    def valid_pair(self, pair):
        if not pair.is_outward_pair():
            return True

        if pair.get_gap_size() > self.minimum_distance:
            return True
        return False


class ReDistanceFilter(FragmentReadPairFilter):
    """
    Filters read pairs where one or both reads are more than
    maximum_distance away from the nearest restriction site.
    """

    def __init__(self, maximum_distance=10000, mask=None):
        super(ReDistanceFilter, self).__init__(mask=mask)
        self.maximum_distance = maximum_distance

    def valid_pair(self, pair):
        """
        Check if any read is >maximum_distance away from RE site.
        """
        d1 = min(abs(pair.left.position - pair.left.fragment.start),
                 abs(pair.left.position - pair.left.fragment.end))
        d2 = min(abs(pair.right.position - pair.right.fragment.start),
                 abs(pair.right.position - pair.right.fragment.end))

        if d1 + d2 > self.maximum_distance:
            return False

        return True


class SelfLigationFilter(FragmentReadPairFilter):
    """
    Filters read pairs where one or both reads are more than
    maximum_distance away from the nearest restriction site.
    """

    def __init__(self, mask=None):
        super(SelfLigationFilter, self).__init__(mask=mask)

    def valid_pair(self, pair):
        """
        Check if any read is >maximum_distance away from RE site.
        """
        if pair.is_same_fragment():
            return False
        return True
