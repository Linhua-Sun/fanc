import argparse
import logging
import os
import os.path
import textwrap
import shutil
import tempfile

# configure logging
logger = logging.getLogger(__name__)


def kaic_parser():
    usage = '''\
        kaic <command> [options]

        Commands:
            auto                Automatically process an entire Hi-C data set
            dirs                Create default folder structure for kaic
            stats               Get statistics for kaic pipeline files

            --- Mapping
            iterative_mapping   Iteratively map a FASTQ file to a Bowtie 2 index

            --- Reads
            load_reads          Load a SAM/BAM file into a Reads object
            filter_reads        Filter a Reads object

            -- Genome
            build_genome        Convenience command to build a Genome object

            --- Pairs
            reads_to_pairs      Convert a Reads object into a Pairs object
            filter_pairs        Filter a Pairs object

            --- Hic
            pairs_to_hic        Convert a pairs object into a Hic object
            filter_hic          Filter a Hic object
            merge_hic           Merge multiple Hic objects
            bin_hic             Bin a Hic object into same-size regions
            correct_hic         Correct a Hic object for biases
            hic_pca             Do a PCA on multiple Hi-C objects
            optimise           Optimise an existing Hic object for faster access
            subset_hic         Create a new Hic object by subsetting

            --- Network
            call_peaks          Call enriched peaks in a Hic object
            filter_peaks        Filter peaks called with 'call_peaks'
            merge_peaks         Merge peaks
            filter_merged_peaks Filter merged peaks

            --- Plotting
            plot_ligation_err   Plot the ligation error of a Pairs object
            plot_re_dist        Plot the distance of reads to the nearest RE site
            plot_hic_corr       Plot the correlation of two Hic objects
            plot_hic_marginals  Plot marginals in a Hic object
            plot_diff           Plot the difference between two Hic matrices

            --- Architecture
            structure_tracks   Calculate structural features of a Hic object
            boundaries         Call boundaries in an Hic object
            fold_change        Create pairwise fold-change Hi-C comparison maps
            average_tracks     Calculate average Hi-C contact profiles per region
            directionality     Calculate directionality index for Hic object
            insulation         Calculate insulation index for Hic object
            diff               Calculate difference between two vectors

            --- Other
            write_config       Write default config file

        Run kaic <command> -h for help on a specific command.
        '''
    parser = argparse.ArgumentParser(
        description="kaic processing tool for Hi-C data",
        usage=textwrap.dedent(usage)
    )

    parser.add_argument(
        '--version', dest='print_version',
        action='store_true',
        help='''Print version information'''
    )
    parser.set_defaults(print_version=False)

    parser.add_argument(
        '--verbose', '-v', dest='verbosity',
        action='count',
        default=0,
        help='''Set verbosity level: Can be chained like '-vvv' to increase verbosity. Default is to show
                        errors, warnings, and info messages (same as '-vv'). '-v' shows only errors and warnings,
                        '-vvv' shows errors, warnings, info, and debug messages in addition.'''
    )

    parser.add_argument(
        '-s', '--silent', dest='silent',
        action='store_true',
        help='''if set, do not print log messages to to command line.'''
    )
    parser.set_defaults(silent=False)

    parser.add_argument(
        '-l', '--log-file', dest='log_file',
        help='''Path to file in which to save log.'''
    )

    parser.add_argument(
        '-m', '--email', dest='email_to_address',
        help='''Email address for kaic command summary.'''
    )

    parser.add_argument(
        '--smtp-server', dest='smtp_server',
        help='''SMTP server in the form smtp.server.com[:port].'''
    )

    parser.add_argument(
        '--smtp-username', dest='smtp_username',
        help='''SMTP username.'''
    )

    parser.add_argument(
        '--smtp-password', dest='smtp_password',
        help='''SMTP password.'''
    )

    parser.add_argument(
        '--smtp-sender-address', dest='email_from_address',
        help='''SMTP sender email address.'''
    )

    parser.add_argument('command', nargs='?', help='Subcommand to run')

    return parser


def auto_parser():
    parser = argparse.ArgumentParser(
        prog="kaic auto",
        description='Automatically process an entire Hi-C data set'
    )

    parser.add_argument(
        'input',
        nargs='+',
        help='''Input files. kaic will try to guess the file by its extension.'''
    )

    parser.add_argument(
        'output_folder',
        help='''Folder where output files and sub-folders will be generated'''
    )

    parser.add_argument(
        '-g', '--genome', dest='genome',
        help='''Can be an HDF5 Genome object, a FASTA file,
                a folder with FASTA files, or a comma-separated
                list of FASTA files.'''
    )

    parser.add_argument(
        '-r', '--restriction-enzyme', dest='restriction_enzyme',
        help='''Restriction enzyme used for digestion (e.g. HindIII, case-sensitive)'''
    )

    parser.add_argument(
        '-i', '--genome-index', dest='genome_index',
        help='''Bowtie 2 genome index. Only required when passing FASTQ files as input'''
    )

    parser.add_argument(
        '-n', '--basename', dest='basename',
        help='''Basename for output files. If not provided, will be guessed based on input file names'''
    )

    parser.add_argument(
        '-s', '--step-size', dest='step_size',
        type=int,
        default=3,
        help='''Step size for iterative mapping. Default: 3'''
    )

    parser.add_argument(
        '-t', '--threads', dest='threads',
        type=int,
        default=1,
        help='''Maximum number of threads to use for the analysis.'''
    )

    parser.add_argument(
        '-O', '--no-optimise', dest='optimise',
        action='store_false',
        help='''Produce a Hi-C object optimised for fast access times. May impact compatibility.'''
    )
    parser.set_defaults(optimise=True)

    parser.add_argument(
        '-x', '--split-fastq', dest='split_fastq',
        action='store_true',
        help='''Run multiple bowtie2 processes in split FASTQ files instead of a single multi-core bowtie2 process.
                Faster, but uses more memory proportional to the number fo threads.'''
    )
    parser.set_defaults(split_fastq=False)

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Copy original files to temporary directory. Reduces network I/O.'''
    )
    parser.set_defaults(copy=False)

    return parser


def auto(argv):
    import os

    parser = auto_parser()
    args = parser.parse_args(argv[2:])

    def is_fastq_file(file_name):
        base, extension = os.path.splitext(file_name)
        if extension in ['.gz', '.gzip']:
            base, extension = os.path.splitext(base)

        if extension in ['.fq', '.fastq']:
            return True
        return False

    def is_sam_or_bam_file(file_name):
        _, extension = os.path.splitext(file_name)
        return extension in ['.sam', '.bam']

    def is_reads_file(file_name):
        _, extension = os.path.splitext(file_name)
        return extension in ['.reads']

    def is_pairs_file(file_name):
        _, extension = os.path.splitext(file_name)
        return extension in ['.pairs']

    def is_hic_file(file_name):
        _, extension = os.path.splitext(file_name)
        return extension in ['.hic']

    def file_type(file_name):
        if is_fastq_file(file_name):
            return 'fastq'
        if is_sam_or_bam_file(file_name):
            return 'sam'
        if is_reads_file(file_name):
            return 'reads'
        if is_pairs_file(file_name):
            return 'pairs'
        if is_hic_file(file_name):
            return 'hic'
        return None

    def file_basename(file_name):
        basename = os.path.basename(os.path.splitext(file_name)[0])
        if basename.endswith('.gz') or basename.endswith('.gzip'):
            basename = os.path.splitext(basename)[0]
        return basename

    file_names = [os.path.expanduser(file_name) for file_name in args.input]
    file_types = [file_type(file_name) for file_name in file_names]
    file_basenames = [file_basename(file_name) for file_name in file_names]

    for i in range(len(file_types)):
        if file_types[i] not in ('fastq', 'sam', 'reads', 'pairs', 'hic'):
            import kaic
            try:
                ds = kaic.load(file_names[i], mode='r')
                if isinstance(ds, kaic.Hic) or isinstance(ds, kaic.AccessOptimisedHic):
                    file_types[i] = 'hic'
                elif isinstance(ds, kaic.Pairs) or isinstance(ds, kaic.FragmentMappedReadPairs):
                    file_types[i] = 'pairs'
                elif isinstance(ds, kaic.Reads):
                    file_types[i] = 'reads'
                else:
                    raise ValueError("Could not detect file type using kaic load.")
            except ValueError:
                raise ValueError("Not a valid input file type: {}".format(file_type))

    if args.basename is None:
        if len(file_basenames) == 1:
            basename = file_basenames[0]
        else:
            basename = []
            for pair in zip(*file_basenames):
                if pair[0] == pair[1]:
                    basename.append(pair[0])
                else:
                    break
            if len(basename) == 0:
                basename = file_basenames[0]
            else:
                if basename[-1] in ['.', '_']:
                    basename = "".join(basename[:-1])
                else:
                    basename = "".join(basename)
    else:
        basename = args.basename

    output_folder = os.path.expanduser(args.output_folder)
    if not output_folder[-1] == '/':
        output_folder += '/'

    threads = args.threads

    # 0. Do some sanity checks on required flags
    if 'fastq' in file_types:
        if args.genome_index is None:
            print("Error: Must provide genome index (-i) when mapping FASTQ files!")
            quit(1)
        else:
            check_path = os.path.expanduser(args.genome_index)
            if check_path.endswith('.'):
                check_path = check_path[:-1]
            for i in range(1, 5):
                if not os.path.exists(check_path + '.{}.bt2'.format(i)):
                    raise ValueError("Cannot find bowtie2 path!")
            for i in range(1, 3):
                if not os.path.exists(check_path + '.rev.{}.bt2'.format(i)):
                    raise ValueError("Bowtie2 index incomplete, check index files for completeness.")

    if 'fastq' in file_types or 'sam' in file_types or 'reads' in file_types:
        if args.genome is None:
            print("Error: Must provide genome (-g) to process read pair files!")
            quit(1)

        if args.restriction_enzyme is None:
            print("Error: Must provide restriction enzyme (-r) to process read pair files!")
            quit(1)
        else:
            from Bio import Restriction
            try:
                getattr(Restriction, args.restriction_enzyme)
            except AttributeError:
                raise ValueError("restriction_enzyme string is not recognized: %s" % args.restriction_enzyme)

    logger.info("Output folder: %s" % output_folder)
    logger.info("Input files: %s" % str(file_names))
    logger.info("Input file types: %s" % str(file_types))

    if args.basename:
        logger.info("Final basename: %s" % basename)
    else:
        logger.info("Final basename: %s (you can change this with the -n option!)" % basename)

    import subprocess
    from multiprocessing.pool import ThreadPool

    # 1. create default folders in root directory
    logger.info("Creating output folders...")
    rc = subprocess.call(['kaic', 'dirs', output_folder])
    if rc != 0:
        print("Creating folders failed for some reason, aborting...")
        quit(rc)

    # 2. If input files are (gzipped) FASTQ, map them iteratively first
    def mapping_worker(file_name, index, bam_file, mapping_threads=1):
        iterative_mapping_command = ['kaic', 'iterative_mapping',
                                     '-m', '25', '-s', str(args.step_size), '-q', '30', '-t', str(mapping_threads)]
        if args.tmp:
            iterative_mapping_command.append('-tmp')

        if args.split_fastq:
            iterative_mapping_command.append('-x')

        return subprocess.call(iterative_mapping_command + [file_name, index, bam_file])

    fastq_files = []
    for i in range(len(file_names)):
        if file_types[i] != 'fastq':
            continue
        fastq_files.append(i)

    if len(fastq_files) > 0:
        mapping_processes = threads
        tp = ThreadPool(1)

        index = os.path.expanduser(args.genome_index)
        if index.endswith('.'):
            index = index[:-1]
        logger.info("Iteratively mapping FASTQ files...")

        bam_files = []
        fastq_results = []
        for i, ix in enumerate(fastq_files):
            bam_file = output_folder + 'sam/' + file_basenames[ix] + '.bam'
            bam_files.append(bam_file)

            fastq_results.append(tp.apply_async(mapping_worker,
                                                (file_names[ix], index, bam_file, mapping_processes)))
        tp.close()
        tp.join()

        for rt in fastq_results:
            if rt.get() != 0:
                raise RuntimeError("Bowtie mapping had non-zero exit status")

        for ix, i in enumerate(fastq_files):
            file_names[i] = bam_files[ix]
            file_types[i] = 'sam'

    # 3. SAM/BAM to Reads object conversion
    def reads_worker(file_name, reads_file):
        load_reads_command = ['kaic', 'load_reads', '-D']
        if args.tmp:
            load_reads_command.append('-tmp')

        return subprocess.call(load_reads_command + [file_name, reads_file])

    sam_files = []
    for i in range(len(file_names)):
        if file_types[i] != 'sam':
            continue
        sam_files.append(i)

    if len(sam_files) > 0:
        tp = ThreadPool(threads)

        reads_files = []
        reads_results = []
        for ix in sam_files:
            reads_file = output_folder + 'reads/' + file_basenames[ix] + '.reads'
            reads_files.append(reads_file)

            rt = tp.apply_async(reads_worker, (file_names[ix], reads_file))
            reads_results.append(rt)
        tp.close()
        tp.join()

        for rt in reads_results:
            if rt.get() != 0:
                raise RuntimeError("Read loading from SAM/BAM had non-zero exit status")

        for ix, i in enumerate(sam_files):
            file_names[i] = reads_files[ix]
            file_types[i] = 'reads'

    # 4. Filter reads
    def filtered_reads_worker(reads_file, filtered_reads_file, filtered_reads_stats_file):
        filter_reads_command = ['kaic', 'filter_reads', '-m', '-us', '-q', '30']
        if args.tmp:
            filter_reads_command.append('-tmp')
        filter_reads_command.append('-s')

        return subprocess.call(filter_reads_command + [filtered_reads_stats_file, reads_file, filtered_reads_file])

    reads_files = []
    for i in range(len(file_names)):
        if file_types[i] != 'reads':
            continue
        reads_files.append(i)

    if len(reads_files) > 0:
        tp = ThreadPool(threads)

        filtered_reads_files = []
        filter_reads_results = []
        for ix in reads_files:
            filtered_reads_file = output_folder + 'reads/filtered/' + file_basenames[ix] + '_filtered.reads'
            filtered_reads_stats_file = output_folder + 'plots/stats/' + file_basenames[ix] + '.reads.stats.pdf'
            filtered_reads_files.append(filtered_reads_file)

            rt = tp.apply_async(filtered_reads_worker,
                                (file_names[ix], filtered_reads_file, filtered_reads_stats_file))
            filter_reads_results.append(rt)
        tp.close()
        tp.join()

        for rt in filter_reads_results:
            if rt.get() != 0:
                raise RuntimeError("Read filtering had non-zero exit status")

        for ix, i in enumerate(reads_files):
            file_names[i] = filtered_reads_files[ix]

    # 5. Reads to Pairs
    def pairs_worker(pairs_file, filtered_reads_file1, filtered_reads_file2, genome, restriction_enzyme):
        logger.info("Creating Pairs object...")
        pairs_command = ['kaic', 'reads_to_pairs', filtered_reads_file1, filtered_reads_file2,
                         genome, restriction_enzyme, pairs_file]
        if args.tmp:
            pairs_command.append('-tmp')
        return subprocess.call(pairs_command)

    reads_file_pairs = []
    i = 0
    while i < len(file_names):
        if file_types[i] == 'reads':
            if not file_types[i + 1] == 'reads':
                raise RuntimeError("Cannot create read pairs, because %s is missing a partner file" % file_names[i])
            reads_file_pairs.append((i, i + 1))
            i += 1
        i += 1

    # get reads file pair basenames
    pair_basenames = [basename + '_' + str(i) for i in range(len(reads_file_pairs))]

    if len(reads_file_pairs) > 0:
        tp = ThreadPool(threads)
        genome = args.genome
        restriction_enzyme = args.restriction_enzyme

        pairs_files = []
        pairs_results = []
        for i, j in reads_file_pairs:
            if len(reads_file_pairs) > 1:
                pairs_file = output_folder + 'pairs/' + pair_basenames[len(pairs_files)] + '.pairs'
            else:
                pairs_file = output_folder + 'pairs/' + basename + '.pairs'
            rt = tp.apply_async(pairs_worker,
                                (pairs_file, file_names[i], file_names[j], genome, restriction_enzyme))
            pairs_results.append(rt)
            pairs_files.append(pairs_file)
        tp.close()
        tp.join()

        for rt in pairs_results:
            if rt.get() != 0:
                raise RuntimeError("Pairs loading from reads had non-zero exit status")

        for ix, read_pair in enumerate(reversed(reads_file_pairs)):
            file_names[read_pair[0]] = pairs_files[ix]
            del file_names[read_pair[1]]
            file_types[read_pair[0]] = 'pairs'
            del file_types[read_pair[1]]

    # 7. Pairs stats and filtering
    def pairs_ligation_error_worker(pairs_file, ligation_error_file):
        ligation_error_command = ['kaic', 'plot_ligation_err']
        return subprocess.call(ligation_error_command + [pairs_file, ligation_error_file])

    def pairs_re_dist_worker(pairs_file, re_dist_file):
        re_dist_command = ['kaic', 'plot_re_dist']
        return subprocess.call(re_dist_command + [pairs_file, re_dist_file])

    def filtered_pairs_worker(pairs_file, filtered_pairs_file, filtered_pairs_stats_file):
        filter_pairs_command = ['kaic', 'filter_pairs', '--auto', '-r', '5000', '-l', '-d', '2']
        if args.tmp:
            filter_pairs_command.append('-tmp')
        filter_pairs_command.append('-s')

        p1 = subprocess.call(filter_pairs_command + [filtered_pairs_stats_file, pairs_file,
                                                     filtered_pairs_file])
        if p1 != 0:
            logger.error("Filtering failed for some reason, trying again with fixed thresholds...")
            filter_pairs_command = ['kaic', 'filter_pairs', '-i', '10000',
                                    '10000', '-r', '5000', '-l', '-d', '2']
            if args.tmp:
                filter_pairs_command.append('-tmp')
            filter_pairs_command.append('-s')
            p1 = subprocess.call(filter_pairs_command + [filtered_pairs_stats_file, pairs_file,
                                                         filtered_pairs_file])
        return p1

    pairs_files = []
    for i in range(len(file_names)):
        if file_types[i] != 'pairs':
            continue
        pairs_files.append(i)

    if len(pairs_files) > 0:
        tp = ThreadPool(threads)

        filtered_pairs_files = []
        filter_pairs_results = []
        for ix in pairs_files:
            pair_basename = os.path.basename(os.path.splitext(file_names[ix])[0])
            filtered_pairs_file = output_folder + 'pairs/filtered/' + pair_basename + '_filtered.pairs'
            filtered_pairs_stats_file = output_folder + 'plots/stats/' + pair_basename + '.pairs.stats.pdf'
            ligation_error_file = output_folder + 'plots/stats/' + pair_basename + '.pairs.ligation_error.pdf'
            re_dist_file = output_folder + 'plots/stats/' + pair_basename + '.pairs.re_dist.pdf'

            tp.apply_async(pairs_ligation_error_worker, (file_names[ix], ligation_error_file))
            tp.apply_async(pairs_re_dist_worker, (file_names[ix], re_dist_file))
            rt = tp.apply_async(filtered_pairs_worker,
                                (file_names[ix], filtered_pairs_file, filtered_pairs_stats_file))
            filter_pairs_results.append(rt)

            filtered_pairs_files.append(filtered_pairs_file)
        tp.close()
        tp.join()

        for rt in filter_pairs_results:
            if rt.get() != 0:
                raise RuntimeError("Pair filtering had non-zero exit status")

        for ix, i in enumerate(pairs_files):
            file_names[i] = filtered_pairs_files[ix]

    # 8. Pairs to Hic
    def hic_worker(pairs_file, hic_file):
        hic_command = ['kaic', 'pairs_to_hic']
        if args.tmp:
            hic_command.append('-tmp')

        return subprocess.call(hic_command + [pairs_file, hic_file])

    pairs_files = []
    for i in range(len(file_names)):
        if file_types[i] != 'pairs':
            continue
        pairs_files.append(i)

    if len(pairs_files) > 0:
        tp = ThreadPool(threads)

        hic_files = []
        hic_results = []
        for ix in pairs_files:
            hic_basename = os.path.basename(os.path.splitext(file_names[ix])[0])
            if hic_basename.endswith('_filtered'):
                hic_basename = hic_basename[:-9]
            hic_file = output_folder + 'hic/' + hic_basename + '.hic'

            rt = tp.apply_async(hic_worker, (file_names[ix], hic_file))
            hic_results.append(rt)

            hic_files.append(hic_file)
        tp.close()
        tp.join()

        for rt in hic_results:
            if rt.get() != 0:
                raise RuntimeError("Hi-C conversion had non-zero exit status")

        for ix, i in enumerate(pairs_files):
            file_names[i] = hic_files[ix]
            file_types[i] = 'hic'

    # 9. Merge Hic
    hic_files = []
    for i in range(len(file_names)):
        if file_types[i] != 'hic':
            continue
        hic_files.append(i)

    if len(hic_files) > 1:
        output_hic = output_folder + 'hic/' + basename + '.hic'
        logger.info("Merging Hi-C files...")
        merge_hic_command = ['kaic', 'merge_hic']
        if args.tmp:
            merge_hic_command.append('-tmp')

        if not args.optimise:
            merge_hic_command.append('-O')

        hics = [file_names[i] for i in hic_files]
        rt = subprocess.call(merge_hic_command + hics + [output_hic])

        if rt != 0:
            raise RuntimeError("Hi-C merge had non-zero exit status")

        file_names[hic_files[0]] = output_hic
        hic_files.pop(0)
        for ix, i in enumerate(reversed(hic_files)):
            del file_names[i]
            del file_types[i]

    import kaic

    # batch worker to:
    # * bin
    # * filter
    # * correct
    def batch_hic_worker(hic_file, bin_size, binned_hic_file, filtered_hic_file, filtered_hic_stats_file,
                         corrected_hic_file, chromosome_corrected_hic_file):
        logger.info("Binning Hic {} at {}...".format(hic_file, bin_size))
        bin_hic_command = ['kaic', 'bin_hic']
        if args.tmp:
            bin_hic_command.append('-tmp')

        ret1 = subprocess.call(bin_hic_command + [hic_file, binned_hic_file, str(bin_size)])
        if ret1 != 0:
            return ret1

        logger.info("Filtering Hic {}...".format(binned_hic_file))
        filter_hic_command = ['kaic', 'filter_hic']
        if args.tmp:
            filter_hic_command.append('-tmp')
        filter_hic_command.append('-rl')
        filter_hic_command.append('0.1')
        filter_hic_command.append('-s')

        ret2 = subprocess.call(filter_hic_command + [filtered_hic_stats_file, binned_hic_file, filtered_hic_file])
        if ret2 != 0:
            return ret2

        logger.info("Correcting Hic {}...".format(filtered_hic_file))
        correct_hic_command = ['kaic', 'correct_hic']
        if args.tmp:
            correct_hic_command.append('-tmp')
        if not args.optimise:
            correct_hic_command.append('-O')

        ret3 = subprocess.call(correct_hic_command + ['-c', filtered_hic_file, chromosome_corrected_hic_file])
        if ret3 != 0:
            return ret3

        hic = kaic.load(filtered_hic_file, mode='r')
        n_regions = len(hic.regions)
        hic.close()

        if n_regions <= 50000:
            ret4 = subprocess.call(correct_hic_command + [filtered_hic_file, corrected_hic_file])
        else:
            ret4 = 0

        if ret4 != 0:
            return ret4

        return 0

    hic_files = []
    for i in range(len(file_names)):
        if file_types[i] != 'hic':
            continue
        hic_files.append(i)

    if len(hic_files) > 0:
        tp = ThreadPool(threads)

        hic_results = []
        for ix in hic_files:
            binned_hic_file_base = output_folder + 'hic/binned/' + basename + '_'

            for bin_size, bin_size_str in [(5000000, '5mb'), (2000000, '2mb'), (1000000, '1mb'),
                                           (500000, '500kb'), (250000, '250kb'), (100000, '100kb'),
                                           (50000, '50kb'), (25000, '25kb'), (10000, '10kb'), (5000, '5kb')]:
                binned_hic_file = binned_hic_file_base + bin_size_str + '.hic'
                hic_basename = os.path.basename(os.path.splitext(binned_hic_file)[0])
                filtered_hic_file = output_folder + 'hic/filtered/' + hic_basename + '_filtered.hic'
                filtered_hic_stats_file = output_folder + 'plots/stats/' + hic_basename + '_filtered.stats.pdf'
                chromosome_corrected_hic_file = output_folder + 'hic/corrected/' + hic_basename + '_corrected_pc.hic'
                corrected_hic_file = output_folder + 'hic/corrected/' + hic_basename + '_corrected.hic'

                rt = tp.apply_async(batch_hic_worker, (file_names[ix],  # hic_file
                                                       bin_size,
                                                       binned_hic_file,
                                                       filtered_hic_file,
                                                       filtered_hic_stats_file,
                                                       corrected_hic_file,
                                                       chromosome_corrected_hic_file))
                hic_results.append(rt)
        tp.close()
        tp.join()

    for rt in hic_results:
        if rt.get() != 0:
            raise RuntimeError("Hi-C binning/filtering/correcting had non-zero exit status")

    return 0


def dirs_parser():
    parser = argparse.ArgumentParser(
        prog="kaic dirs",
        description='Automatically process an entire Hi-C data set'
    )

    parser.add_argument(
        'root_directory',
        help='''Root directory in which to create kaic folders'''
    )
    return parser


def dirs(argv):
    parser = dirs_parser()

    args = parser.parse_args(argv[2:])

    root_dir = os.path.expanduser(args.root_directory)

    import errno

    try:
        os.makedirs(root_dir)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

    try:
        os.makedirs(root_dir + '/fastq')
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

    try:
        os.makedirs(root_dir + '/sam')
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

    try:
        os.makedirs(root_dir + '/reads/filtered')
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

    try:
        os.makedirs(root_dir + '/pairs/filtered')
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

    try:
        os.makedirs(root_dir + '/hic/filtered')
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

    try:
        os.makedirs(root_dir + '/hic/binned')
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

    try:
        os.makedirs(root_dir + '/hic/corrected')
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

    try:
        os.makedirs(root_dir + '/plots/stats')
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

    try:
        os.makedirs(root_dir + '/plots/matrix')
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise


def iterative_mapping_parser():
    parser = argparse.ArgumentParser(
        prog="kaic iterative_mapping",
        description='Iteratively map a FASTQ file to a Bowtie 2 index'
    )

    parser.add_argument(
        'input',
        nargs='+',
        help='''File name of the input FASTQ file (or gzipped FASTQ)'''
    )

    parser.add_argument(
        'index',
        help='''Bowtie 2 genome index'''
    )

    parser.add_argument(
        'output',
        help='''Output file name (or folder name if multiple input files provided)'''
    )

    parser.add_argument(
        '-m', '--min-size', dest='min_size',
        type=int,
        default=25,
        help='''Minimum length of read before extension. Default 25.'''
    )

    parser.add_argument(
        '-s', '--step-size', dest='step_size',
        type=int,
        default=2,
        help='''Number of base pairs to extend at each round of mapping. Default is 2.'''
    )

    parser.add_argument(
        '-t', '--threads', dest='threads',
        type=int,
        default=1,
        help='''Number of threads used for mapping'''
    )

    parser.add_argument(
        '-q', '--quality', dest='quality',
        type=int,
        default=30,
        help='''Mapping quality cutoff for reads to be sent to another iteration'''
    )

    parser.add_argument(
        '-w', '--work-dir', dest='work_dir',
        help='''Working directory, defaults to the system temporary folder'''
    )

    parser.add_argument(
        '-r', '--restriction-enzyme', dest='restriction_enzyme',
        help='''Name of restriction enzyme used in experiment.
                                If provided, will trim reads at resulting ligation junction.'''
    )

    parser.add_argument(
        '-b', '--batch-size', dest='batch_size',
        type=int,
        default=1000000,
        help='''Number of reads processed (mapped and merged) in one go.'''
    )

    parser.add_argument(
        '-x', '--split-fastq', dest='split_fastq',
        action='store_true',
        help='''Parallelise by spawning multiple bowtie2 processes rather than a
                        single multi-core bowtie2 process.'''
    )
    parser.set_defaults(split_fastq=False)

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='copy',
        action='store_true',
        help='''Copy original file to working directory (see -w option). Reduces network I/O.'''
    )
    parser.set_defaults(copy=False)

    return parser


def iterative_mapping(argv):
    parser = iterative_mapping_parser()
    args = parser.parse_args(argv[2:])

    # check arguments
    index_path = os.path.expanduser(args.index)
    output_folder = os.path.expanduser(args.output)

    step_size = args.step_size
    min_size = args.min_size
    threads = args.threads
    batch_size = args.batch_size
    split_fastq = args.split_fastq

    from kaic.mapping.iterative_mapping import split_iteratively_map_reads
    from kaic.tools.general import mkdir

    for input_file in args.input:
        input_file = os.path.expanduser(input_file)
        if len(args.input) == 1:
            output_file = output_folder
        else:
            output_folder = mkdir(output_folder)
            basename, extension = os.path.splitext(os.path.basename(input_file))
            output_file = output_folder + basename + '.bam'
        split_iteratively_map_reads(input_file, output_file, index_path, work_dir=args.work_dir,
                                    quality_cutoff=args.quality, batch_size=batch_size, threads=threads,
                                    min_size=min_size, step_size=step_size, copy=args.copy,
                                    restriction_enzyme=args.restriction_enzyme, bowtie_parallel=not split_fastq)


def load_reads_parser():
    parser = argparse.ArgumentParser(
        prog="kaic load_reads",
        description='Load a SAM/BAM file into a Reads object'
    )

    parser.add_argument(
        'input',
        help='''Input SAM file'''
    )

    parser.add_argument(
        'output',
        help='''Output file'''
    )

    parser.add_argument(
        '-N', '--ignore-qname', dest='qname',
        action='store_false',
        help='''Do not store a read's qname, only a hashed version will be stored internally.'''
    )
    parser.set_defaults(qname=True)

    parser.add_argument(
        '-Q', '--ignore-qual', dest='qual',
        action='store_false',
        help='''Do not store a read's quality string.'''
    )
    parser.set_defaults(qual=True)

    parser.add_argument(
        '-S', '--ignore-seq', dest='seq',
        action='store_false',
        help='''Do not store a read's sequence string.'''
    )
    parser.set_defaults(seq=True)

    parser.add_argument(
        '-C', '--ignore-cigar', dest='cigar',
        action='store_false',
        help='''Do not store a read's cigar string. Warning: Some filters rely on this attribute.'''
    )
    parser.set_defaults(cigar=True)

    parser.add_argument(
        '-T', '--ignore-tags', dest='tags',
        action='store_false',
        help='''Do not store a read's tags. Warning: Some filters rely on this attribute.'''
    )
    parser.set_defaults(tags=True)

    parser.add_argument(
        '-D', '--ignore-default', dest='ignore_default',
        action='store_true',
        help='''Ignore qname, seq, and qual information to speed up read loading.'''
    )
    parser.set_defaults(ignore_default=False)

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)

    return parser


def load_reads(argv):
    parser = load_reads_parser()
    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.tools.files import create_temporary_copy

    input_path = os.path.expanduser(args.input)
    if args.tmp:
        logger.info("Creating copy to work in temporary folder...")
        input_path = create_temporary_copy(input_path, preserve_extension=True)
        logger.info("Copy created in %s" % input_path)

    output_path = os.path.expanduser(args.output)
    original_output_path = output_path
    if args.tmp:
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_file.close()
        output_path = tmp_file.name
        logger.info("Output temporarily redirected to %s" % output_path)

    if args.ignore_default is True:
        store_qname = False
        store_seq = False
        store_qual = False
        store_cigar = True
        store_tags = True
    else:
        store_qname = args.qname
        store_seq = args.seq
        store_qual = args.qual
        store_cigar = args.cigar
        store_tags = args.tags

    logger.info("Starting to import file %s" % input_path)
    reads = kaic.Reads(file_name=output_path, mode='w')
    reads.load(sambam=input_path, store_cigar=store_cigar, store_seq=store_seq, store_qname=store_qname,
               store_qual=store_qual, store_tags=store_tags, sample_size=100000)
    reads.close()

    if args.tmp:
        logger.info("Removing temporary file...")
        os.unlink(input_path)
        logger.info("Moving output file to destination...")
        shutil.move(output_path, original_output_path)
    logger.info("All done.")


def filter_reads_parser():
    parser = argparse.ArgumentParser(
        prog="kaic filter_reads",
        description='Filter a Reads object'
    )

    parser.add_argument(
        'input',
        help='''Input Reads file'''
    )

    parser.add_argument(
        'output',
        nargs="?",
        help='''Output Reads file. If not provided will filter existing file directly.'''
    )

    parser.add_argument(
        '-m', '--mapped', dest='mapped',
        action='store_true',
        help='''Filter unmapped reads'''
    )
    parser.set_defaults(mapped=False)

    parser.add_argument(
        '-u', '--unique', dest='unique',
        action='store_true',
        help='''Filter reads that map multiple times (with a lower score)'''
    )
    parser.set_defaults(unique=False)

    parser.add_argument(
        '-us', '--unique-strict', dest='unique_strict',
        action='store_true',
        help='''Strictly filter reads that map multiple times (XS tag)'''
    )
    parser.set_defaults(unique_strict=False)

    parser.add_argument(
        '-q', '--quality', dest='quality',
        type=int,
        help='''Cutoff for the minimum mapping quality of a read'''
    )

    parser.add_argument(
        '-c', '--contaminant', dest='contaminant',
        help='''A Reads file with contaminating reads. Will filter out reads with the same name.'''
    )

    parser.add_argument(
        '-s', '--stats', dest='stats',
        help='''Path for saving stats pdf'''
    )

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)

    return parser


def filter_reads(argv):
    parser = filter_reads_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.tools.files import copy_or_expand, create_temporary_copy
    from kaic.construct.seq import ContaminantFilter

    # copy file if required
    original_input_path = os.path.expanduser(args.input)
    if args.tmp:
        logger.info("Copying Reads object to temporary file...")
        input_path = create_temporary_copy(original_input_path)
        logger.info("Temporarily working in %s" % input_path)
    else:
        input_path = copy_or_expand(args.input, args.output)

    reads = kaic.Reads(file_name=input_path, mode='a')

    if args.mapped:
        logger.info("Unmapped filter enabled")
        reads.filter_unmapped(queue=True)

    if args.unique_strict:
        logger.info("Strict multi-map filter enabled")
        reads.filter_non_unique(strict=True, queue=True)
    elif args.unique:
        logger.info("Soft multi-map filter enabled")
        reads.filter_non_unique(strict=False, queue=True)

    if args.quality:
        logger.info("Quality filter enabled (%d)" % args.quality)
        reads.filter_quality(args.quality, queue=True)

    if args.contaminant:
        contaminant_file = os.path.expanduser(args.contaminant)
        logger.info("Contaminant filter enabled %s" % contaminant_file)
        contaminant = kaic.Reads(contaminant_file)
        contaminant_filter = ContaminantFilter(contaminant,
                                               reads.add_mask_description("contaminant",
                                                                          "Filter contaminating reads"))
        reads.filter(contaminant_filter, queue=True)

    logger.info("Running filters...")
    reads.run_queued_filters(log_progress=True)
    logger.info("Done.")

    if args.stats:
        logger.info("Plotting filter statistics")
        from kaic.plotting.plot_statistics import plot_mask_statistics
        plot_mask_statistics(reads, reads._reads, output=args.stats)
        logger.info("Done.")

    reads.close()

    if args.tmp:
        output_path = os.path.expanduser(args.output)
        if os.path.isdir(output_path):
            output_path = "%s/%s" % (output_path, os.path.basename(original_input_path))
        logger.info("Moving temporary output file to destination %s..." % output_path)
        shutil.move(input_path, output_path)

    logger.info("All done.")


def build_genome_parser():
    parser = argparse.ArgumentParser(
        prog="kaic build_genome",
        description='Convenience command to build a Genome object'
    )

    parser.add_argument(
        'input',
        nargs='+',
        help=textwrap.dedent('''\
                             Can be a FASTA file,
                             a folder with FASTA files, or a
                             list of FASTA files.
                             ''')
    )

    parser.add_argument(
        'output',
        help='''Output file for Genome object'''
    )

    return parser


def build_genome(argv):
    parser = build_genome_parser()
    args = parser.parse_args(argv[2:])

    genome_string = ','.join(args.input)
    output_path = os.path.expanduser(args.output)

    import kaic

    logger.info("Building Genome...")
    genome = kaic.Genome.from_string(genome_string=genome_string, file_name=output_path)
    genome.close()
    logger.info("All done.")


def reads_to_pairs_parser():
    parser = argparse.ArgumentParser(
        prog="kaic reads_to_pairs",
        description='Convert a Reads object into a Pairs object'
    )

    parser.add_argument(
        'reads1',
        help='''First half of input reads'''
    )

    parser.add_argument(
        'reads2',
        help='''Second half of input reads'''
    )

    parser.add_argument(
        'genome',
        help=textwrap.dedent('''\
                                     Can be an HDF5 Genome object, a FASTA file,
                                     a folder with FASTA files, or a comma-separated
                                     list of FASTA files.
                                     ''')
    )

    parser.add_argument(
        'restriction_enzyme',
        help='''Restriction enzyme used in the experiment, e.g. HindIII'''
    )

    parser.add_argument(
        'output',
        help='''Output file for mapped pairs'''
    )

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)

    return parser


def reads_to_pairs(argv):
    parser = reads_to_pairs_parser()
    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.tools.files import create_temporary_copy

    reads1_path = os.path.expanduser(args.reads1)
    # copy file if required
    if args.tmp:
        logger.info("Creating temporary copy of first half of reads...")
        reads1_path = create_temporary_copy(reads1_path)
        logger.info("Working with temporary copy %s" % reads1_path)

    reads2_path = os.path.expanduser(args.reads2)
    # copy file if required
    if args.tmp:
        logger.info("Creating temporary copy of second half of reads...")
        reads2_path = create_temporary_copy(reads2_path)
        logger.info("Working with temporary copy %s" % reads2_path)

    genome_path = os.path.expanduser(args.genome)

    output_path = os.path.expanduser(args.output)
    original_output_path = output_path
    if args.tmp:
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_file.close()
        output_path = tmp_file.name
        logger.info("Working in temporary output file %s" % output_path)

    logger.info("Loading left side of reads...")
    reads1 = kaic.Reads(reads1_path, mode='r')
    logger.info("Loading right side of reads...")
    reads2 = kaic.Reads(reads2_path, mode='r')
    logger.info("Building genome...")
    genome = kaic.Genome.from_string(genome_path)
    logger.info("Getting fragments...")
    nodes = genome.get_regions(args.restriction_enzyme)

    logger.info("Building pairs...")
    pairs = kaic.Pairs(file_name=output_path, mode='w')
    logger.info("Mapping reads...")
    pairs.load(reads1, reads2, nodes)

    reads1.close()
    reads2.close()
    pairs.close()

    if args.tmp:
        logger.info("Removing temporary input files...")
        os.unlink(reads1_path)
        os.unlink(reads2_path)
        logger.info("Moving output file to destination %s" % original_output_path)
        shutil.move(output_path, original_output_path)

    logger.info("All done.")


def filter_pairs_parser():
    parser = argparse.ArgumentParser(
        prog="kaic filter_pairs",
        description='Filter a Pairs object'
    )

    parser.add_argument(
        'input',
        help='''Input FragmentMappedPairs file'''
    )

    parser.add_argument(
        'output',
        nargs="?",
        help='''Output FragmentMappedPairs file. If not provided will filter input file in place.'''
    )

    parser.add_argument(
        '-i', '--inward', dest='inward',
        type=int,
        help='''Minimum distance for inward-facing read pairs'''
    )

    parser.add_argument(
        '-o', '--outward', dest='outward',
        type=int,
        help='''Minimum distance for outward-facing read pairs'''
    )

    parser.add_argument(
        '--auto',
        action='store_true',
        help='''Auto-guess settings for inward/outward read pair filters.
                        Overrides --outward and --inward if set.'''
    )

    parser.add_argument(
        '-r', '--re-distance', dest='redist',
        type=int,
        help='''Maximum distance for a read to the nearest restriction site'''
    )

    parser.add_argument(
        '-l', '--self-ligated', dest='self_ligated',
        action='store_true',
        help='''Remove read pairs representing self-ligated fragments'''
    )
    parser.set_defaults(self_ligated=False)

    parser.add_argument(
        '-d', '--duplicate', dest='dup_thresh',
        type=int,
        help='''If specified, filter read pairs for PCR duplicates. Parameter determines
                        distance between alignment starts below which they are considered Starting
                        at same position. Sensible values are between 1 and 5.'''
    )

    parser.add_argument(
        '-s', '--stats', dest='stats',
        help='''Path for saving stats pdf'''
    )

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)

    return parser


def filter_pairs(argv):
    parser = filter_pairs_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.tools.files import copy_or_expand, create_temporary_copy

    # copy file if required
    original_input_path = os.path.expanduser(args.input)
    if args.tmp:
        logger.info("Creating temporary copy of input file...")
        input_path = create_temporary_copy(original_input_path)
        logger.info("Working from copy in %s" % input_path)
    else:
        input_path = copy_or_expand(args.input, args.output)

    pairs = kaic.load(file_name=input_path, mode='a')

    if args.auto:
        logger.info("Filtering inward- and outward-facing reads using automatically"
                    "determined thresholds.")
        pairs.filter_ligation_products(queue=True)
    else:
        if args.inward:
            logger.info("Filtering inward-facing reads at %dbp" % args.inward)
            pairs.filter_inward(minimum_distance=args.inward, queue=True)

        if args.outward:
            logger.info("Filtering outward-facing reads at %dbp" % args.outward)
            pairs.filter_outward(minimum_distance=args.outward, queue=True)

    if args.redist:
        logger.info("Filtering reads with RE distance >%dbp" % args.redist)
        pairs.filter_re_dist(args.redist, queue=True)

    if args.self_ligated:
        logger.info("Filtering self-ligated read pairs")
        pairs.filter_self_ligated(queue=True)

    if args.dup_thresh:
        logger.info("Filtering PCR duplicates, threshold <=%dbp" % args.dup_thresh)
        pairs.filter_pcr_duplicates(threshold=args.dup_thresh, queue=True)

    logger.info("Running filters...")
    pairs.run_queued_filters(log_progress=True)
    logger.info("Done.")

    if args.stats:
        logger.info("Plotting filter statistics")
        from kaic.plotting.plot_statistics import plot_mask_statistics
        plot_mask_statistics(pairs, pairs._pairs, output=args.stats)
        logger.info("Done.")

    pairs.close()

    if args.tmp:
        output_path = os.path.expanduser(args.output)
        if os.path.isdir(output_path):
            output_path = "%s/%s" % (output_path, os.path.basename(original_input_path))
        logger.info("Moving temporary output file to destination %s..." % output_path)
        shutil.move(input_path, output_path)

    logger.info("All done.")


def pairs_to_hic_parser():
    parser = argparse.ArgumentParser(
        prog="kaic pairs_to_hic",
        description='Convert a pairs object into a Hic object'
    )

    parser.add_argument(
        'pairs',
        help='''Input Pairs file'''
    )

    parser.add_argument(
        'hic',
        help='''Output path for Hic file'''
    )

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)
    return parser


def pairs_to_hic(argv):
    parser = pairs_to_hic_parser()
    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.tools.files import create_temporary_copy

    original_pairs_path = os.path.expanduser(args.pairs)
    original_hic_path = os.path.expanduser(args.hic)
    if args.tmp:
        logger.info("Creating temporary copy of input file...")
        pairs_path = create_temporary_copy(original_pairs_path)
        logger.info("Working from temporary input file %s" % pairs_path)
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_file.close()
        hic_path = tmp_file.name
        logger.info("Working in temporary output file %s" % hic_path)
    else:
        pairs_path = original_pairs_path
        hic_path = original_hic_path

    logger.info("Loading pairs...")
    pairs = kaic.load(pairs_path, mode='r')
    logger.info("Done.")

    hic = pairs.to_hic(file_name=hic_path)
    logger.info("Done.")

    pairs.close()
    hic.close()

    if args.tmp:
        logger.info("Removing temporary input file...")
        os.unlink(pairs_path)
        logger.info("Moving output file to destination %s" % original_hic_path)
        shutil.move(hic_path, original_hic_path)

    logger.info("All done.")


def merge_hic_parser():
    parser = argparse.ArgumentParser(
        prog="kaic merge_hic",
        description='Merge multiple Hic objects'
    )

    parser.add_argument(
        'hic',
        nargs='+',
        help='''Input Hic files'''
    )

    parser.add_argument(
        'output',
        help='''Output binned Hic object'''
    )

    parser.add_argument(
        '-O', '--no-optimise', dest='optimise',
        action='store_false',
        help='''Produce a Hi-C object optimised for fast access times. May impact compatibility.'''
    )
    parser.set_defaults(optimise=True)

    parser.add_argument(
        '--intra', dest='intra',
        action='store_true',
        help='''Only merge intra-chromosomal contacts and set inter-chromosomal to 0.'''
    )
    parser.set_defaults(intra=False)

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)

    return parser


def merge_hic(argv):
    parser = merge_hic_parser()
    args = parser.parse_args(argv[2:])
    import tempfile
    tmpdir = tempfile.gettempdir() if args.tmp else None

    import kaic

    output_path = os.path.expanduser(args.output)
    paths = [os.path.expanduser(path) for path in args.hic]
    hics = [kaic.load_hic(path, mode='r', tmpdir=tmpdir) for path in paths]

    # try fast, basic loading first:
    try:
        if args.optimise:
            merged = kaic.AccessOptimisedHic.from_hic(hics, file_name=output_path, tmpdir=tmpdir,
                                                      only_intrachromosomal=args.intra)
        else:
            merged = kaic.Hic.from_hic(hics, file_name=output_path, tmpdir=tmpdir,
                                       only_intrachromosomal=args.intra)

        merged.close()
    except ValueError:
        logging.warning("The regions in your Hi-C objects do not appear to be identical. This will slow down"
                        "merging significantly.")
        first_hic = hics.pop(0)

        if args.optimise:
            merged = kaic.AccessOptimisedHic(data=first_hic, file_name=output_path, mode='w', tmpdir=tmpdir)
        else:
            merged = kaic.Hic(data=first_hic, file_name=output_path, mode='w', tmpdir=tmpdir)

        merged.merge(hics)

        merged.close()

    for hic in hics:
        hic.close()

    logger.info("All done")


def filter_hic_parser():
    parser = argparse.ArgumentParser(
        prog="kaic filter_hic",
        description='Filter a Hic object'
    )

    parser.add_argument(
        'input',
        help='''Input Hic file'''
    )

    parser.add_argument(
        'output',
        nargs="?",
        help='''Output Hic file. If not provided will filter input file in place.'''
    )

    parser.add_argument(
        '-l', '--low-coverage', dest='low',
        type=float,
        help='''Filter bins with "low coverage" (lower than specified absolute contact threshold)'''
    )

    parser.add_argument(
        '-rl', '--relative-low-coverage', dest='rel_low',
        type=float,
        help='''Filter bins using a relative "low coverage" threshold
                    (lower than the specified fraction of the median contact count)'''
    )

    parser.add_argument(
        '-ld', '--low-coverage-default', dest='low_default',
        action='store_true',
        help='''Filter bins with "low coverage" (under 10%% of median coverage for all non-zero bins)'''
    )
    parser.set_defaults(low_default=False)

    parser.add_argument(
        '-d', '--diagonal', dest='diagonal',
        type=int,
        help='''Filter bins along the diagonal up to this specified distance'''
    )

    parser.add_argument(
        '-s', '--stats', dest='stats',
        help='''Path for saving stats pdf'''
    )

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)

    return parser


def filter_hic(argv):
    parser = filter_hic_parser()
    args = parser.parse_args(argv[2:])
    import kaic
    from kaic.tools.files import copy_or_expand, create_temporary_copy

    original_input_path = os.path.expanduser(args.input)
    if args.tmp:
        logger.info("Creating temporary copy of input file...")
        input_path = create_temporary_copy(original_input_path)
        logger.info("Working from copy in %s" % input_path)
    else:
        input_path = copy_or_expand(args.input, args.output)

    with kaic.load_hic(file_name=input_path, mode='a') as hic:
        if args.low_default:
            if args.low or args.rel_low:
                logger.info("Already specified an cutoff with -l or -rl, skipping -ld...")
            else:
                logger.info("Filtering low-coverage bins at 10%%")
                hic.filter_low_coverage_regions(rel_cutoff=0.1, cutoff=None, queue=True)

        if (args.low is not None or args.rel_low is not None) and args.low_default is False:
            logger.info("Filtering low-coverage bins using absolute cutoff {:.4}, "
                        "relative cutoff {:.1%}".format(float(args.low) if args.low else 0.,
                                                        float(args.rel_low) if args.rel_low else 0.))
            hic.filter_low_coverage_regions(cutoff=args.low, rel_cutoff=args.rel_low, queue=True)

            if args.diagonal is not None:
                logger.info("Filtering diagonal at distance %d" % args.diagonal)
                hic.filter_diagonal(distance=args.diagonal, queue=True)

            logger.info("Running filters...")
            hic.run_queued_filters(log_progress=True)
            logger.info("Done.")

            if args.stats:
                logger.info("Plotting filter statistics")
                from kaic.plotting.plot_statistics import plot_mask_statistics
                plot_mask_statistics(hic, hic._edges, output=args.stats)
                logger.info("Done.")

    if args.tmp:
        output_path = os.path.expanduser(args.output)
        if os.path.isdir(output_path):
            output_path = "%s/%s" % (output_path, os.path.basename(original_input_path))
        logger.info("Moving temporary output file to destination %s..." % output_path)
        shutil.move(input_path, output_path)

    logger.info("All done.")


def bin_hic_parser():
    parser = argparse.ArgumentParser(
        prog="kaic bin_hic",
        description='Bin a Hic object into same-size regions'
    )

    parser.add_argument(
        'hic',
        help='''Input Hic file'''
    )

    parser.add_argument(
        'output',
        help='''Output binned Hic object'''
    )

    parser.add_argument(
        'bin_size',
        type=int,
        help='''Bin size in base pairs'''
    )

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)
    return parser


def bin_hic(argv):
    parser = bin_hic_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.tools.files import create_temporary_copy

    original_output_path = os.path.expanduser(args.output)
    if args.tmp:
        input_path = create_temporary_copy(args.hic)
        logger.info("Working in temporary directory...")
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_file.close()
        output_path = tmp_file.name
        logger.info("Temporary output file: %s" % output_path)
    else:
        input_path = os.path.expanduser(args.hic)
        output_path = original_output_path

    hic = kaic.load_hic(file_name=input_path, mode='r')

    logger.info("Binning at %dbp" % args.bin_size)
    binned = hic.bin(args.bin_size, file_name=output_path)

    hic.close()
    binned.close()

    if args.tmp:
        logger.info("Moving temporary output file to destination %s" % original_output_path)
        os.unlink(input_path)
        shutil.move(output_path, original_output_path)

    logger.info("All done.")


def correct_hic_parser():
    parser = argparse.ArgumentParser(
        prog="kaic correct_hic",
        description='Correct a Hic object for biases'
    )

    parser.add_argument(
        'input',
        help='''Input Hic file'''
    )

    parser.add_argument(
        'output',
        nargs="?",
        help='''Output Hic file. If not provided will filter existing file in place.'''
    )

    parser.add_argument(
        '-i', '--ice', dest='ice',
        action='store_true',
        help='''Use ICE iterative correction instead of Knight matrix balancing'''
    )
    parser.set_defaults(ice=False)

    parser.add_argument(
        '-c', '--chromosome', dest='chromosome',
        action='store_true',
        help='''Correct intra-chromosomal data individually, ignore inter-chromosomal data'''
    )
    parser.set_defaults(chromosome=False)

    parser.add_argument(
        '-O', '--no-optimise', dest='optimise',
        action='store_false',
        help='''Produce a Hi-C object optimised for fast access times. May impact compatibility.'''
    )
    parser.set_defaults(optimise=True)

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)
    return parser


def correct_hic(argv):
    parser = correct_hic_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.tools.files import create_temporary_copy

    # copy file if required
    original_input_path = os.path.expanduser(args.input)
    original_output_path = os.path.expanduser(args.output)
    if args.tmp:
        logger.info("Copying data to temporary file...")
        input_path = create_temporary_copy(original_input_path)
        logger.info("Working from temporary file %s" % input_path)
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_file.close()
        output_path = tmp_file.name
        logger.info("Temporary output file: %s" % output_path)
    else:
        input_path = os.path.expanduser(args.input)
        output_path = os.path.expanduser(args.output)

    if args.ice:
        import kaic.correcting.ice_matrix_balancing as ice
        hic = kaic.load_hic(file_name=input_path, mode='a')
        ice.correct(hic)
        hic.close()
        if args.tmp:
            logger.info("Moving temporary output file to destination %s" % original_output_path)
            shutil.move(input_path, original_output_path)
    else:
        import kaic.correcting.knight_matrix_balancing as knight

        hic = kaic.load_hic(file_name=input_path, mode='r')
        hic_new = knight.correct(hic, only_intra_chromosomal=args.chromosome,
                                 copy=True, file_name=output_path, optimise=args.optimise)
        hic.close()
        hic_new.close()
        if args.tmp:
            logger.info("Moving temporary output file to destination %s" % original_output_path)
            os.unlink(input_path)
            shutil.move(output_path, original_output_path)

    logger.info("All done.")


def hic_pca_parser():
    parser = argparse.ArgumentParser(
        prog="kaic hic_pca",
        description='Do a PCA on multiple Hi-C objects'
    )

    parser.add_argument(
        'input',
        nargs='+',
        help='''Input Hic files'''
    )

    parser.add_argument(
        'output_folder',
        help='''Output folder for PCA results.'''
    )

    parser.add_argument(
        '-s', '--sample-sizes', dest='sample_sizes',
        nargs='+',
        type=int,
        default=[50000],
        help='''Sample sizes for contacts to do the PCA on.'''
    )

    parser.add_argument(
        '-i', '--intra', dest='intra',
        action='store_true',
        help='''Only do PCA on intra-chromosomal contacts'''
    )
    parser.set_defaults(intra=False)

    parser.add_argument(
        '-d', '--divide', dest='divide',
        action='store_true',
        help='''Divide PCAs into individual chromosomes'''
    )
    parser.set_defaults(divide=False)

    parser.add_argument(
        '-e', '--expected-filter', dest='expected_filter',
        type=float,
        help='''Cutoff for expected/observed ratio of a contact to be considered for PCA. Default: no filter.'''
    )

    parser.add_argument(
        '-b', '--background-filter', dest='background_filter',
        type=float,
        help='''Cutoff for ratio of average inter-chromosomal to
                    observed contact to be considered for PCA. Default: no filter.'''
    )

    parser.add_argument(
        '-w', '--window-filter', dest='window_filter',
        nargs=2,
        type=int,
        help='''Min and max values in base pairs defining a window of
                    contact distances that are retained for analysis.'''
    )

    parser.add_argument(
        '-n', '--names', dest='names',
        nargs='+',
        help='''Sample names for plot labelling.'''
    )

    parser.add_argument(
        '-p', '--pair-selection', dest='pair_selection',
        default='variance',
        help='''Mechanism to select pairs from Hi-C matrix. Default: variance.
                    Possible values are:
                    variance: Selects pairs with the largest variance across samples first.
                    fc: Select pairs with the largest fold-change across samples first.
                    passthrough: Selects pairs without preference.
                 '''
    )

    parser.add_argument(
        '-c', '--colors', dest='colors',
        nargs='+',
        help='''Colors for plotting.'''
    )

    parser.add_argument(
        '-m', '--markers', dest='markers',
        nargs='+',
        help='''Markers for plotting. Follows Matplotlib marker
                    definitions: http://matplotlib.org/api/markers_api.html'''
    )

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)
    return parser


def hic_pca(argv):
    parser = hic_pca_parser()

    args = parser.parse_args(argv[2:])

    import errno
    import matplotlib
    matplotlib.use('pdf')
    import kaic
    from kaic.plotting.plot_statistics import pca_plot
    from kaic.architecture.pca import do_pca, HicCollectionWeightMeanVariance, PassthroughPairSelection, \
        LargestVariancePairSelection, LargestFoldChangePairSelection
    from kaic.architecture.hic_architecture import ExpectedObservedCollectionFilter,\
        BackgroundLigationCollectionFilter, MinMaxDistanceCollectionFilter
    from kaic.tools.files import create_temporary_copy
    import shutil

    hics = []
    output_folder = os.path.expanduser(args.output_folder)

    try:
        os.makedirs(output_folder)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

    if args.tmp:
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_file.close()
        output_path = tmp_file.name
    else:
        output_path = output_folder + '/hics.coll'

    sample_names = args.names if args.names is not None else []
    try:
        if len(args.input) == 1:
            shutil.copy(args.input[0], output_path)
            coll = HicCollectionWeightMeanVariance(output_path)
        else:

            for file_name in args.input:
                if args.names is None:
                    sample_names.append(os.path.splitext(os.path.basename(file_name))[0])

                if args.tmp:
                    input_path = create_temporary_copy(file_name)
                else:
                    input_path = os.path.expanduser(file_name)
                hics.append(kaic.load_hic(input_path, mode='r'))

            coll = HicCollectionWeightMeanVariance(hics, file_name=output_path, only_intra_chromosomal=args.intra,
                                                   scale_libraries=True, mode='w')
            coll.calculate()

        # filtering
        if args.expected_filter is not None:
            eof = ExpectedObservedCollectionFilter(coll, fold_change=args.expected_filter)
            coll.filter(eof, queue=True)
        if args.background_filter is not None:
            bgf = BackgroundLigationCollectionFilter(coll, all_contacts=True, fold_change=args.background_filter)
            coll.filter(bgf, queue=True)
        if args.window_filter is not None:
            mmdf = MinMaxDistanceCollectionFilter(coll, min_distance=args.window_filter[0],
                                                  max_distance=args.window_filter[1])
            coll.filter(mmdf, queue=True)
        coll.run_queued_filters()

        if args.pair_selection == 'variance':
            pair_selector = LargestVariancePairSelection()
        elif args.pair_selection == 'fc':
            pair_selector = LargestFoldChangePairSelection()
        elif args.pair_selection == 'passthrough':
            pair_selector = PassthroughPairSelection()
        else:
            raise ValueError("Pair selection mechanism {} is not valid".format(args.pair_selection))

        if args.divide:
            regions = coll.chromosomes()
        else:
            regions = [None]

        for sample_size in args.sample_sizes:
            for region in regions:
                logger.info("Sample size: %d" % sample_size)
                pca_info, pca_res = do_pca(coll, pair_selection=pair_selector, sample_size=sample_size,
                                           regions=region)

                with open(output_folder + "/explained_variance_{}_{}.txt".format(region, sample_size), 'w') as var:
                    for i, variance in enumerate(pca_info.explained_variance_ratio_):
                        var.write(str(variance))
                        if i == len(pca_info.explained_variance_ratio_)-1:
                            var.write("\n")
                        else:
                            var.write("\t")

                with open(output_folder + "/pca_results_{}_{}.txt".format(region, sample_size), 'w') as res:
                    for i, row in enumerate(pca_res):
                        for j, value in enumerate(row):
                            res.write(str(value))
                            if j == len(row)-1:
                                res.write("\n")
                            else:
                                res.write("\t")

                fig, ax = pca_plot(pca_res, pca_info=pca_info, colors=args.colors, names=sample_names,
                                   markers=args.markers)
                ax.set_title("PCA sample size %d" % sample_size)
                fig.savefig(output_folder + "/pca_plot_{}_{}.pdf".format(region, sample_size))
                fig.clf()
    finally:
        if args.tmp:
            for hic in hics:
                file_name = hic.file.filename
                hic.close()
                os.unlink(file_name)
            shutil.move(output_path, output_folder + '/hics.coll')


def call_peaks_parser():
    parser = argparse.ArgumentParser(
        prog="kaic call_peaks",
        description='Call enriched peaks in a Hic object'
    )

    parser.add_argument(
        'input',
        help='''Input Hic file'''
    )

    parser.add_argument(
        'output',
        help='''Output HDF5 file'''
    )

    parser.add_argument(
        '-c', '--chromosomes', dest='chromosomes',
        nargs='+',
        help='''Chromosomes to be investigated.'''
    )

    parser.add_argument(
        '-p', '--peak-size', dest='peak_size',
        type=int,
        help='''Size of the expected peak in pixels. If not set, will be estimated to correspond to ~ 25kb.'''
    )

    parser.add_argument(
        '-w', '--width', dest='width',
        type=int,
        help='''Width of the investigated area surrounding a peak in pixels. If not set, will be estimated at p+3'''
    )

    parser.add_argument(
        '-m', '--min-dist', dest='min_dist',
        type=int,
        default=3,
        help='''Minimum distance in pixels for two loci to be considered as peaks. Default: 3'''
    )

    parser.add_argument(
        '-t', '--threads', dest='threads',
        type=int,
        default=4,
        help='''Number of threads for parallel processing. Default: 4'''
    )

    parser.add_argument(
        '-b', '--batch-size', dest='batch_size',
        type=int,
        default=500000,
        help='''Maximum number of peaks examined per process. Default: 500,000'''
    )

    parser.add_argument(
        '-o', '--observed-cutoff', dest='o_cutoff',
        type=int,
        default=1,
        help='''Minimum observed contacts at peak (in reads).'''
    )

    parser.add_argument(
        '-ll', '--lower-left-cutoff', dest='ll_cutoff',
        type=float,
        default=1.0,
        help='''Minimum enrichment of peak compared to lower-left neighborhood (observed/e_ll > cutoff).'''
    )

    parser.add_argument(
        '-z', '--horizontal-cutoff', dest='h_cutoff',
        type=float,
        default=1.0,
        help='''Minimum enrichment of peak compared to horizontal neighborhood (observed/e_h > cutoff).'''
    )

    parser.add_argument(
        '-v', '--vertical-cutoff', dest='v_cutoff',
        type=float,
        default=1.0,
        help='''Minimum enrichment of peak compared to vertical neighborhood (observed/e_v > cutoff).'''
    )

    parser.add_argument(
        '-d', '--donut-cutoff', dest='d_cutoff',
        type=float,
        default=1.0,
        help='''Minimum enrichment of peak compared to donut neighborhood (observed/e_d > cutoff).'''
    )

    parser.add_argument(
        '-i', '--inter-chromosomal', dest='inter',
        action='store_true',
        help='''If set, also find peaks in inter-chromosomal data.'''
    )
    parser.set_defaults(inter=False)

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)
    return parser


def call_peaks(argv):
    parser = call_peaks_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    import kaic.data.network as kn
    from kaic.tools.files import create_temporary_copy

    # copy file if required
    original_input_path = os.path.expanduser(args.input)
    original_output_path = os.path.expanduser(args.output)
    if args.tmp:
        logger.info("Copying data to temporary file...")
        input_path = create_temporary_copy(original_input_path)
        logger.info("Working from temporary file %s" % input_path)
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_file.close()
        output_path = tmp_file.name
        logger.info("Temporary output file: %s" % output_path)
    else:
        input_path = os.path.expanduser(args.input)
        output_path = os.path.expanduser(args.output)

    pk = kn.RaoPeakCaller(p=args.peak_size, w_init=args.width, min_locus_dist=args.min_dist,
                          observed_cutoff=args.o_cutoff, n_processes=args.threads,
                          batch_size=args.batch_size, process_inter=args.inter, e_ll_cutoff=args.ll_cutoff,
                          e_d_cutoff=args.d_cutoff, e_h_cutoff=args.h_cutoff, e_v_cutoff=args.v_cutoff)

    hic = kaic.load_hic(input_path, mode='r')

    if args.chromosomes is None:
        chromosome_pairs = None
    else:
        chromosome_pairs = []
        for i in range(len(args.chromosomes)):
            chromosome1 = args.chromosomes[i]
            for j in range(i, len(args.chromosomes)):
                chromosome2 = args.chromosomes[j]
                chromosome_pairs.append((chromosome1, chromosome2))

    peaks = pk.call_peaks(hic, chromosome_pairs=chromosome_pairs, file_name=output_path)

    logger.info("Found %d potential peaks" % len(peaks))
    peaks.close()

    if args.tmp:
        os.unlink(input_path)
        logger.info("Moving temporary output file to destination %s" % original_output_path)
        shutil.move(output_path, original_output_path)


def filter_peaks_parser():
    parser = argparse.ArgumentParser(
        prog="kaic filter_peaks",
        description='Filter peaks called with call_peaks'
    )

    parser.add_argument(
        'input',
        help='''Input Peaks file'''
    )

    parser.add_argument(
        'output',
        nargs='?',
        help='''Output filtered Peaks file'''
    )

    parser.add_argument(
        '-f', '--fdr', dest='fdr_cutoff',
        type=float,
        help='''Global FDR cutoff - overrides cutoffs set with --fdr-donut, etc. Value between 0 and 1.'''
    )

    parser.add_argument(
        '-fd', '--fdr-donut', dest='fdr_donut_cutoff',
        type=float,
        default=0.1,
        help='''Donut neighborhood FDR cutoff. Value between 0 and 1. Default=0.1'''
    )

    parser.add_argument(
        '-fh', '--fdr-horizontal', dest='fdr_horizontal_cutoff',
        type=float,
        default=0.1,
        help='''Horizontal neighborhood FDR cutoff. Value between 0 and 1. Default=0.1'''
    )

    parser.add_argument(
        '-fv', '--fdr-vertical', dest='fdr_vertical_cutoff',
        type=float,
        default=0.1,
        help='''Vertical neighborhood FDR cutoff. Value between 0 and 1. Default=0.1'''
    )

    parser.add_argument(
        '-fl', '--fdr-lower-left', dest='fdr_lower_left_cutoff',
        type=float,
        default=0.1,
        help='''Lower-left neighborhood FDR cutoff. Value between 0 and 1. Default=0.1'''
    )

    parser.add_argument(
        '-e', '--enrichment', dest='enrichment',
        type=float,
        help='''Global enrichment cutoff. Value between 0 and infinity,
                    e.g. 2.0 means two-fold enrichment over every contact neighborhood.
                    Overrides cutoffs set with --e-donut, etc.'''
    )

    parser.add_argument(
        '-ed', '--enrichment-donut', dest='enrichment_donut',
        type=float,
        default=2.0,
        help='''Donut enrichment cutoff. Value between 0 and infinity. Default=2.0'''
    )

    parser.add_argument(
        '-eh', '--enrichment-horizontal', dest='enrichment_horizontal',
        type=float,
        default=1.5,
        help='''Horizontal enrichment cutoff. Value between 0 and infinity. Default=1.5'''
    )

    parser.add_argument(
        '-ev', '--enrichment-vertical', dest='enrichment_vertical',
        type=float,
        default=1.5,
        help='''Vertical enrichment cutoff. Value between 0 and infinity. Default=1.5'''
    )

    parser.add_argument(
        '-el', '--enrichment-lower_left', dest='enrichment_lower_left',
        type=float,
        default=1.75,
        help='''Lower left enrichment cutoff. Value between 0 and infinity. Default=1.75'''
    )

    parser.add_argument(
        '-r', '--rao', dest='rao',
        action='store_true',
        help='''Filter peaks as Rao et al. (2014) does. It only retains peaks that

                        1. are at least 2-fold enriched over either the donut or lower-left neighborhood
                        2. are at least 1.5-fold enriched over the horizontal and vertical neighborhoods
                        3. are at least 1.75-fold enriched over both the donut and lower-left neighborhood
                        4. have an FDR <= 0.1 in every neighborhood

                    Warning: this flag overrides all other filters in this run!
            '''
    )
    parser.set_defaults(rao=False)

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)
    return parser


def filter_peaks(argv):
    parser = filter_peaks_parser()

    args = parser.parse_args(argv[2:])

    import kaic.data.network as kn
    from kaic.tools.files import create_temporary_copy, copy_or_expand

    # copy file if required
    original_input_path = os.path.expanduser(args.input)
    if args.tmp:
        logger.info("Copying data to temporary file...")
        input_path = create_temporary_copy(original_input_path)
    else:
        input_path = copy_or_expand(args.input, args.output)

    peaks = kn.RaoPeakInfo(input_path, mode='a')

    if args.rao:
        logger.info("Running Rao filter")
        peaks.filter_rao()
    else:
        if args.fdr_cutoff is not None:
            logger.info("Global FDR filter at %.f" % args.fdr_cutoff)
            peaks.filter_fdr(args.fdr_cutoff, queue=True)
        else:
            logger.info("Local FDR filter")
            fdr_mask = peaks.add_mask_description('fdr', 'FDR cutoff filter')
            fdr_filter = kn.FdrPeakFilter(fdr_ll_cutoff=args.fdr_lower_left_cutoff,
                                          fdr_d_cutoff=args.fdr_donut_cutoff,
                                          fdr_h_cutoff=args.fdr_horizontal_cutoff,
                                          fdr_v_cutoff=args.fdr_vertical_cutoff,
                                          mask=fdr_mask)
            peaks.filter(fdr_filter, queue=True)

        if args.enrichment is not None:
            logger.info("Global enrichment filter at %.f" % args.enrichment)
            peaks.filter_observed_expected_ratio(ll_ratio=args.enrichment, d_ratio=args.enrichment,
                                                 v_ratio=args.enrichment, h_ratio=args.enrichment,
                                                 queue=True)
        else:
            logger.info("Local enrichment filter")
            peaks.filter_observed_expected_ratio(ll_ratio=args.enrichment_lower_left,
                                                 d_ratio=args.enrichment_donut,
                                                 v_ratio=args.enrichment_vertical,
                                                 h_ratio=args.enrichment_horizontal,
                                                 queue=True)
            peaks.peak_table.run_queued_filters()
    peaks.close()

    if args.tmp:
        output_path = os.path.expanduser(args.output)
        if os.path.isdir(output_path):
            output_path = "%s/%s" % (output_path, os.path.basename(original_input_path))
        logger.info("Moving temporary output file to destination %s..." % output_path)
        shutil.move(input_path, output_path)

    logger.info("All done.")


def merge_peaks_parser():
    parser = argparse.ArgumentParser(
        prog="kaic merge_peaks",
        description='Filter peaks called with call_peaks'
    )

    parser.add_argument(
        'input',
        help='''Input Peaks file'''
    )

    parser.add_argument(
        'output',
        help='''Output merged Peaks file'''
    )

    parser.add_argument(
        '-d', '--distance', dest='distance',
        type=int,
        default=20000,
        help='''Maximum distance in base pairs at which to merge two peaks. Default 20000bp'''
    )

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)
    return parser


def merge_peaks(argv):
    parser = merge_peaks_parser()

    args = parser.parse_args(argv[2:])

    import kaic.data.network as kn
    from kaic.tools.files import create_temporary_copy

    # copy file if required
    original_input_path = os.path.expanduser(args.input)
    original_output_path = os.path.expanduser(args.output)
    if args.tmp:
        logger.info("Copying data to temporary file...")
        input_path = create_temporary_copy(original_input_path)
        logger.info("Working from temporary file %s" % input_path)
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_file.close()
        output_path = tmp_file.name
        logger.info("Temporary output file: %s" % output_path)
    else:
        input_path = os.path.expanduser(args.input)
        output_path = os.path.expanduser(args.output)

    peaks = kn.RaoPeakInfo(input_path, mode='r')
    peaks.merged_peaks(output_path, euclidian_distance=args.distance)

    if args.tmp:
        os.unlink(input_path)
        logger.info("Moving temporary output file to destination %s" % original_output_path)
        shutil.move(output_path, original_output_path)


def filter_merged_peaks_parser():
    parser = argparse.ArgumentParser(
        prog="kaic filter_merged_peaks",
        description='Filter merged peaks'
    )

    parser.add_argument(
        'input',
        help='''Input merged Peaks file'''
    )

    parser.add_argument(
        'output',
        nargs='?',
        help='''Output filtered merged Peaks file'''
    )

    parser.add_argument(
        '-r', '--rao', dest='rao',
        action='store_true',
        help='''Filter peaks as Rao et al. (2014) does.
                    It removes peaks that are singlets and have a q-value sum >.02.
            '''
    )
    parser.set_defaults(rao=False)

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)
    return parser


def filter_merged_peaks(argv):
    parser = filter_merged_peaks_parser()

    args = parser.parse_args(argv[2:])

    import kaic.data.network as kn
    from kaic.tools.files import create_temporary_copy, copy_or_expand

    # copy file if required
    original_input_path = os.path.expanduser(args.input)
    if args.tmp:
        logger.info("Copying data to temporary file...")
        input_path = create_temporary_copy(original_input_path)
    else:
        input_path = copy_or_expand(args.input, args.output)

    merged_peaks = kn.PeakInfo(input_path, mode='a')

    if args.rao:
        logger.info("Running Rao filter")
        merged_peaks.filter_rao()

    if args.tmp:
        output_path = os.path.expanduser(args.output)
        if os.path.isdir(output_path):
            output_path = "%s/%s" % (output_path, os.path.basename(original_input_path))
        logger.info("Moving temporary output file to destination %s..." % output_path)
        shutil.move(input_path, output_path)

    logger.info("All done.")


def plot_ligation_err_parser():
    parser = argparse.ArgumentParser(
        prog="kaic plot_ligation_err",
        description='Plot the ligation structure biases of a Pairs object'
    )

    parser.add_argument(
        'input',
        help='''Input Pairs file'''
    )

    parser.add_argument(
        'output',
        nargs='?',
        help='''Output pdf'''
    )

    parser.add_argument(
        '-p', '--points', dest='points',
        type=int,
        help='''Data points that make up one increment of the x axis. More=smoother=less detail.'''
    )

    return parser


def plot_ligation_err(argv):
    parser = plot_ligation_err_parser()
    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.plotting.plot_statistics import hic_ligation_structure_biases_plot

    input_path = os.path.expanduser(args.input)
    output_path = None
    if args.output:
        output_path = os.path.expanduser(args.output)

    pairs = kaic.load(file_name=input_path, mode='r')
    hic_ligation_structure_biases_plot(pairs, output=output_path, sampling=args.points)
    pairs.close()

    logger.info("All done.")


def plot_re_dist_parser():
    parser = argparse.ArgumentParser(
        prog="kaic plot_re_dist",
        description='Plot the restriction site distance of reads in a Pairs object'
    )

    parser.add_argument(
        'input',
        help='''Input Pairs file'''
    )

    parser.add_argument(
        'output',
        nargs='?',
        help='''Output pdf'''
    )

    parser.add_argument(
        '-l', '--limit', dest='limit',
        type=int,
        default=10000,
        help='''Limit the plot to the first LIMIT read pairs for the sake of speed. Default 10000'''
    )

    parser.add_argument(
        '-m', '--max-dist', dest='max_dist',
        type=int,
        help='''Maximum RE site distance to include in the plot. Default: no max'''
    )

    return parser


def plot_re_dist(argv):
    parser = plot_re_dist_parser()
    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.plotting.plot_statistics import pairs_re_distance_plot

    input_path = os.path.expanduser(args.input)
    output_path = None
    if args.output:
        output_path = os.path.expanduser(args.output)

    pairs = kaic.load(file_name=input_path, mode='r')
    pairs_re_distance_plot(pairs, output=output_path, limit=args.limit, max_distance=args.max_dist)
    pairs.close()

    logger.info("All done.")


def plot_hic_corr_parser():
    parser = argparse.ArgumentParser(
        prog="kaic plot_hic_corr",
        description='Plot the correlation of two Hic objects'
    )

    parser.add_argument(
        'hic1',
        help='''First Hi-C file'''
    )

    parser.add_argument(
        'hic2',
        help='''Second Hi-C file'''
    )

    parser.add_argument(
        'output',
        nargs="?",
        help='''Output PDF file'''
    )

    parser.add_argument(
        '-c', '--colormap', dest='colormap',
        help='''Matplotlib colormap'''
    )
    return parser


def plot_hic_corr(argv):
    parser = plot_hic_corr_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.config import config
    from kaic.plotting.plot_genomic_data import hic_correlation_plot

    colormap = config.colormap_hic if args.colormap is None else args.colormap

    hic1_path = os.path.expanduser(args.hic1)
    hic2_path = os.path.expanduser(args.hic2)

    hic1 = kaic.load_hic(hic1_path, mode='r')
    hic2 = kaic.load_hic(hic2_path, mode='r')

    output_path = None
    if args.output:
        output_path = os.path.expanduser(args.output)

    hic_correlation_plot(hic1, hic2, output=output_path, colormap=colormap, size=15)

    hic1.close()
    hic2.close()
    logger.info("All done.")


def plot_hic_marginals_parser():
    parser = argparse.ArgumentParser(
        prog="kaic plot_hic_marginals",
        description='Plot Hic matrix marginals'
    )

    parser.add_argument(
        'input',
        help='''Input Hi-C file'''
    )

    parser.add_argument(
        'output',
        nargs="?",
        help='''Output PDF file'''
    )

    parser.add_argument(
        '-l', '--lower', dest='lower',
        type=float,
        help='''Plot lower coverage bound at this level'''
    )

    parser.add_argument(
        '-u', '--upper', dest='upper',
        type=float,
        help='''Plot lower coverage bound at this level'''
    )
    return parser


def plot_hic_marginals(argv):
    parser = plot_hic_marginals_parser()
    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.plotting.plot_genomic_data import hic_marginals_plot

    input_path = os.path.expanduser(args.input)

    hic = kaic.load_hic(input_path, mode='r')

    output_path = None
    if args.output:
        output_path = os.path.expanduser(args.output)

    hic_marginals_plot(hic, output=output_path, lower=args.lower, upper=args.upper)
    hic.close()
    logger.info("All done.")


def structure_tracks_parser():
    parser = argparse.ArgumentParser(
        prog="kaic structure_tracks",
        description='Calculate genomic tracks about structural features of the Hi-C map'
    )
    parser.add_argument(
        'hic',
        help='Input Hic file'
    )

    parser.add_argument(
        'output',
        help='Output path for genomic track'
    )

    parser.add_argument(
        'window_size',
        type=int,
        nargs='+',
        help='Window sizes (in base pairs) used for directionality index,'
             'insulation index and relative insulation index.'
    )

    parser.add_argument(
        '-oe', '--observed_expected',
        action='store_true',
        help='Use observed over expected heatmap for calculations.'
    )

    parser.add_argument(
        '--no_imputation',
        action="store_true",
        help='Do not use imputation to guess value of unmappable bins. '
             'Turning off imputation may lead to artifacts '
             'near unmappable bins. The mask threshold should '
             'be set to a very low value (.1 or so) in this case.'
    )

    parser.add_argument(
        '-n', '--normalise', dest='normalise',
        action='store_true',
        help='''Normalise index values'''
    )
    parser.set_defaults(normalise=False)

    parser.add_argument(
        '-nw', '--normalisation_window',
        type=int, default=300,
        help='Window size for calculating long-range mean for normalization of insulation_index,'
             ' relative_insulation_index, contact_band.'
             'Default 300 bins.'
    )

    parser.add_argument(
        '-o', '--offset',
        type=int, default=0,
        help='Offset of insulation index window from the diagonal in base pairs.'
    )

    parser.add_argument(
        '-w', '--smoothing_window',
        type=int, default=15,
        help='Window size for smoothing derivatives in Savitzky Golay filter (in bins).'
             'Default 15. Must be an odd number.'
    )

    parser.add_argument(
        '-p', '--poly_order',
        type=int, default=2,
        help='Order of polynomial used for smoothing derivatives. Default 2.'
    )

    parser.add_argument(
        '-d', '--derivative',
        type=int,
        nargs='+',
        help='Optionally save derivatives of the specified order (>1).'
    )

    parser.add_argument(
        '--delta',
        type=int,
        nargs='+',
        help='Save delta transformation of metrics according to Crane et al. 2015. '
             'Specify window size in bins. Sensible values for 5kb Drosophila 5-10.'
    )

    parser.add_argument(
        '-ii', '--insulation_index',
        action='store_true',
        help='Calculate insulation index for the given distances (in bins).'
    )

    parser.add_argument(
        '-di', '--directionality_index',
        action='store_true',
        help='Calculate the directionality index for the given distances (in bp)'
    )

    parser.add_argument(
        '-r', '--relative',
        action='store_true',
        help='Calculate the relative insulation indices for the given distances (in bins)'
    )
    return parser


def structure_tracks(argv):
    parser = structure_tracks_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    import numpy as np
    from kaic.architecture.hic_architecture import InsulationIndex, DirectionalityIndex, ObservedExpectedRatio
    from kaic.architecture.genome_architecture import GenomicTrack
    from kaic.tools.matrix import delta_window
    from scipy.signal import savgol_filter

    input_path = os.path.expanduser(args.hic)
    output_path = os.path.expanduser(args.output)

    logger.info("Fetching Hi-C matrix")
    hic_matrix = kaic.load(input_path)

    if args.observed_expected:
        logger.info("Calculating observed/expected ratio")
        hic_matrix = ObservedExpectedRatio(hic_matrix)

    try:
        os.remove(output_path)
    except OSError:
        pass

    # prepare genomic track object
    gt = GenomicTrack(output_path, regions=hic_matrix.regions)

    # calculate insulation index
    if args.insulation_index:
        with InsulationIndex(hic_matrix, relative=args.relative, offset=args.offset,
                             normalise=args.normalise, window_sizes=args.window_size,
                             _normalisation_window=args.normalisation_window) as ii:
            for window_size in args.window_size:
                insulation_index = ii.insulation_index(window_size)
                gt.add_data("insulation_index_{}".format(window_size), insulation_index)

    # calculate directioality index
    if args.directionality_index:
        with DirectionalityIndex(hic_matrix, window_sizes=args.window_size) as di:
            for window_size in args.window_size:
                directionality_index = di.directionality_index(window_size)
                gt.add_data("directionality_index_{}".format(window_size), directionality_index)

    # calculate derivatives, if requested
    if args.derivative or args.delta:
        for k, v in gt.tracks.items():
            if args.derivative:
                for i in args.derivative:
                    if "matrix" in k:
                        deriv_matrix = np.vstack([savgol_filter(x, window_length=args.smoothing_window,
                                                                polyorder=args.poly_order, deriv=i) for x in v.T]).T
                        gt.add_data("{}_d{}".format(k, i), deriv_matrix)
                    else:
                        d = savgol_filter(v, window_length=args.smoothing_window,
                                          polyorder=args.poly_order, deriv=i)
                        gt.add_data("{}_d{}".format(k, i), d)
            if args.delta:
                for i in args.delta:
                    if "matrix" in k:
                        delta_matrix = np.vstack([delta_window(x, i) for x in v.T]).T
                        gt.add_data("{}_delta{}".format(k, i), delta_matrix)
                    else:
                        gt.add_data("{}_delta{}".format(k, i), delta_window(v, i))
    logger.info("All done.")


def boundaries_parser():
    parser = argparse.ArgumentParser(
        prog="kaic boundaries",
        description='Determine structural boundaries'
    )
    parser.add_argument(
        'architecture',
        help='Input InsulationIndex file'
    )
    parser.add_argument(
        'output',
        help="Output folder for boundary BED files (default or when using '-r' option) or "
             "path for boundary BED file (when using -w option)."
    )
    parser.add_argument(
        '-r', '--range', dest='range',
        type=int,
        nargs=2,
        help='Range of insulation index window sizes (<low> <high>) to calculate boundaries on.'
    )
    parser.add_argument(
        '-w', '--window', dest='window',
        type=int,
        help='Insulation index window size to calculate boundaries on'
    )
    parser.add_argument(
        '-d', '--delta', dest='delta',
        type=int, default=7,
        help='Window size for calculating the delta vector (in bins). Default 7.'
    )
    parser.add_argument(
        '-s', '--min-score', dest='min_score',
        type=float,
        help='Report only peaks where the two surrounding extrema of the delta vector have '
             'at least this difference in height. Default: no threshold.'
    )
    parser.add_argument(
        '-p', '--prefix', dest='prefix',
        default='boundaries',
        help='''Output file prefix. Not necessary when using 'w' modus. Default: boundaries'''
    )
    parser.add_argument(
        '-l', '--log', dest='log',
        action='store_true',
        help='''log-transform index values before boundary calling.'''
    )
    parser.set_defaults(log=False)
    return parser


def boundaries(argv):
    parser = boundaries_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.tools.general import mkdir

    input_file = os.path.expanduser(args.architecture)
    output_path = os.path.expanduser(args.output)

    array = kaic.load(input_file, mode='r')

    single = False
    window_sizes = []
    if args.range is not None:
        for window_size in array.window_sizes:
            if args.range[0] <= window_size <= args.range[1]:
                window_sizes.append(window_size)
    elif args.window is not None:
        if args.window in array.window_sizes:
            window_sizes.append(args.window)
        single = True
    else:
        window_sizes = array.window_sizes

    if len(window_sizes) == 0:
        raise ValueError("No valid window size specified!")

    def _to_bed(bs, file_name):
        with open(file_name, 'w') as bed:
            for b in bs:
                bed.write("{}\t{}\t{}\t.\t{}\n".format(b.chromosome, b.start, b.end, b.score))

    if not single:
        mkdir(output_path)
        for window_size in window_sizes:
            logger.info("Processing window size: {}".format(window_size))
            boundaries = array.boundaries(window_size, min_score=args.min_score,
                                          delta_window=args.delta, log=args.log)
            _to_bed(boundaries, output_path + "/{}_{}.bed".format(args.prefix, window_size))
    else:
        boundaries = array.boundaries(window_sizes[0], min_score=args.min_score,
                                      delta_window=args.delta, log=args.log)
        _to_bed(boundaries, output_path)

    logger.info("All done.")


def fold_change_parser():
    parser = argparse.ArgumentParser(
        prog="kaic fold_change",
        description='Create pairwise fold-change Hi-C comparison maps'
    )
    parser.add_argument(
        'input',
        nargs=2,
        help='Input Hic files'
    )
    parser.add_argument(
        'output',
        help='Output FoldChangeMatrix file.'
    )

    parser.add_argument(
        '-S', '--no-scale', dest='scale',
        action='store_false',
        help='''Do not scale input matrices'''
    )
    parser.set_defaults(scale=True)

    parser.add_argument(
        '-l', '--log2', dest='log',
        action='store_true',
        help='''Log2-convert fold-change values'''
    )
    parser.set_defaults(log=False)

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)
    return parser


def fold_change(argv):
    parser = fold_change_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.architecture.hic_architecture import FoldChangeMatrix
    import os.path

    tmpdir = None
    if args.tmp:
        import tempfile
        tmpdir = tempfile.gettempdir()

    hic1 = kaic.load_hic(os.path.expanduser(args.input[0]), mode='r')
    hic2 = kaic.load_hic(os.path.expanduser(args.input[1]), mode='r')

    output_file = os.path.expanduser(args.output)
    with FoldChangeMatrix(hic1, hic2, file_name=output_file, tmpdir=tmpdir, mode='w',
                          scale_matrices=args.scale, log2=args.log) as fcm:
        fcm.calculate()


def average_tracks_parser():
    parser = argparse.ArgumentParser(
        prog="kaic average_tracks",
        description='Calculate average Hi-C contact profiles per region'
    )
    parser.add_argument(
        'input',
        help='Input matrix (Hi-C, fold-change map, ...)'
    )
    parser.add_argument(
        'output',
        help='Output RegionContactAverage file.'
    )

    parser.add_argument(
        '-w', '--window-sizes', dest='window_sizes',
        nargs='+',
        type=int,
        default=[200000, 400000, 600000, 1000000],
        help='''Window sizes in base pairs to calculate region average in.
                    The total window size is composed of the left window plus the right window, i.e. 2x this value.'''
    )

    parser.add_argument(
        '-o', '--offset', dest='offset',
        type=int,
        default=0,
        help='''Window offset in base pairs from the diagonal.'''
    )

    parser.add_argument(
        '-p', '--padding', dest='padding',
        type=int,
        default=1,
        help='''Padding (in number of regions) to calculate average on larger regions.
                    Acts similarly to curve smooting'''
    )

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)

    parser.add_argument(
        '-i', '--impute', dest='impute',
        action='store_true',
        help='''Impute missing values in matrix'''
    )
    parser.set_defaults(impute=False)
    return parser


def average_tracks(argv):
    parser = average_tracks_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.architecture.hic_architecture import RegionContactAverage
    import os.path
    import tempfile

    input_file = os.path.expanduser(args.input)
    output_file = os.path.expanduser(args.output)
    tmpdir = None
    if args.tmp:
        tmpdir = tempfile.gettempdir()

    matrix = kaic.load(input_file, mode='r')

    with RegionContactAverage(matrix, file_name=output_file, tmpdir=tmpdir, window_sizes=args.window_sizes,
                              offset=args.offset, padding=args.padding, impute_missing=args.impute) as rca:
        rca.calculate()


def directionality_parser():
    parser = argparse.ArgumentParser(
        prog="kaic directionality",
        description='Calculate directionality index for Hic object'
    )
    parser.add_argument(
        'input',
        help='Input matrix (Hi-C, fold-change map, ...)'
    )
    parser.add_argument(
        'output',
        help='Output DirectionalityIndex file.'
    )

    parser.add_argument(
        '-w', '--window-sizes', dest='window_sizes',
        nargs='+',
        type=int,
        default=[200000, 400000, 600000, 1000000],
        help='''Window sizes in base pairs to calculate directionality index on.
                    The total window size is composed of the left window plus the right window, i.e. 2x this value.'''
    )

    parser.add_argument(
        '-r', '--region', dest='region',
        help='''Region selector (<chr>:<start>-<end>) to only calculate DI for this region.'''
    )

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)

    parser.add_argument(
        '-i', '--impute', dest='impute',
        action='store_true',
        help='''Impute missing values in matrix'''
    )
    parser.set_defaults(impute=False)
    return parser


def directionality(argv):
    parser = directionality_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.architecture.hic_architecture import DirectionalityIndex
    import os.path
    import tempfile

    input_file = os.path.expanduser(args.input)
    output_file = os.path.expanduser(args.output)
    tmpdir = None
    if args.tmp:
        tmpdir = tempfile.gettempdir()

    matrix = kaic.load(input_file, mode='r')

    with DirectionalityIndex(matrix, file_name=output_file, tmpdir=tmpdir, window_sizes=args.window_sizes) as di:
        di.calculate()


def insulation_parser():
    parser = argparse.ArgumentParser(
        prog="kaic insulation",
        description='Calculate insulation index for Hic object'
    )
    parser.add_argument(
        'input',
        help='Input matrix (Hi-C, fold-change map, ...)'
    )
    parser.add_argument(
        'output',
        help='Output InsulationIndex file.'
    )

    parser.add_argument(
        '-w', '--window-sizes', dest='window_sizes',
        nargs='+',
        type=int,
        default=[200000, 400000, 600000, 1000000],
        help='''Window sizes in base pairs to calculate insulation index on.
                    The total window size is composed of the left window plus the right window, i.e. 2x this value.'''
    )

    parser.add_argument(
        '-r', '--region', dest='region',
        help='''Region selector (<chr>:<start>-<end>) to only calculate II for this region.'''
    )

    parser.add_argument(
        '-tmp', '--work-in-tmp', dest='tmp',
        action='store_true',
        help='''Work in temporary directory'''
    )
    parser.set_defaults(tmp=False)

    parser.add_argument(
        '-i', '--impute', dest='impute',
        action='store_true',
        help='''Impute missing values in matrix'''
    )
    parser.set_defaults(impute=False)

    parser.add_argument(
        '-o', '--offset', dest='offset',
        type=int,
        default=0,
        help='''Window offset in base pairs from the diagonal.'''
    )

    parser.add_argument(
        '-l', '--relative', dest='relative',
        action='store_true',
        help='''Calculate II relative to surrounding region'''
    )
    parser.set_defaults(relative=False)

    parser.add_argument(
        '-log', '--log', dest='log',
        action='store_true',
        help='''Log2-transform II'''
    )
    parser.set_defaults(log=False)

    parser.add_argument(
        '-n', '--normalise', dest='normalise',
        action='store_true',
        help='''Normalise index to insulation average (default is per-chromosome - to normalise to
                    smaller regions, use -nw).'''
    )
    parser.set_defaults(normalise=False)

    parser.add_argument(
        '-nw', '--normalisation-window', dest='normalisation_window',
        type=int,
        help='''Size of the normalisation window (moving average) in bins. Default: whole chromosome.'''
    )

    parser.add_argument(
        '-s', '--subtract-mean', dest='subtract',
        action='store_true',
        help='''Subtract mean instead of dividing by it when '--normalise' is enabled.
                    Useful for log-transformed data'''
    )
    parser.set_defaults(subtract=False)
    return parser


def insulation(argv):
    parser = insulation_parser()

    args = parser.parse_args(argv[2:])

    import kaic
    from kaic.architecture.hic_architecture import InsulationIndex
    import os.path
    import tempfile

    input_file = os.path.expanduser(args.input)
    output_file = os.path.expanduser(args.output)
    tmpdir = None
    if args.tmp:
        tmpdir = tempfile.gettempdir()

    matrix = kaic.load(input_file, mode='r')

    with InsulationIndex(matrix, file_name=output_file, tmpdir=tmpdir, window_sizes=args.window_sizes,
                         impute_missing=args.impute, normalise=args.normalise, offset=args.offset,
                         relative=args.relative, mode='w', subtract_mean=args.subtract, log=args.log,
                         _normalisation_window=args.normalisation_window) as ii:
        ii.calculate()


def optimise_parser():
    parser = argparse.ArgumentParser(
        prog="kaic optimise",
        description='Optimise a Hic object for faster access'
    )
    parser.add_argument(
        'input',
        help='Input Hic file'
    )
    parser.add_argument(
        'output',
        help='Output AccessOptimisedHic file.'
    )
    return parser


def optimise(argv):
    parser = optimise_parser()
    args = parser.parse_args(argv[2:])

    import kaic
    import os.path
    old_hic = kaic.load_hic(os.path.expanduser(args.input), mode='r')
    new_hic = kaic.AccessOptimisedHic(old_hic, file_name=os.path.expanduser(args.output))
    new_hic.close()
    old_hic.close()


def subset_parser():
    parser = argparse.ArgumentParser(
        prog="kaic subset",
        description='Create a new Hic object by subsetting'
    )
    parser.add_argument(
        'input',
        help='Input Hic file'
    )
    parser.add_argument(
        'output',
        help='Output Hic file.'
    )

    parser.add_argument(
        'regions',
        nargs='+'
    )
    return parser


def subset_hic(argv):
    parser = subset_parser()
    args = parser.parse_args(argv[2:])

    import os.path
    import kaic

    input_file = os.path.expanduser(args.input)
    output_file = os.path.expanduser(args.output)

    old_hic = kaic.load_hic(input_file, mode='r')
    new_hic = kaic.AccessOptimisedHic(file_name=output_file, mode='w')

    ix_converter = {}
    ix = 0
    for region_string in args.regions:
        for region in old_hic.subset(region_string):
            ix_converter[region.ix] = ix
            ix += 1

            new_hic.add_region(region, flush=False)
    new_hic.flush()

    for region_string in args.regions:
        for edge in old_hic.edge_subset(key=(region_string, region_string), lazy=True):
            source = ix_converter[edge.source]
            sink = ix_converter[edge.sink]
            new_hic.add_edge([source, sink, edge.weight], flush=False)
    new_hic.flush()


def diff_parser():
    parser = argparse.ArgumentParser(
        prog="kaic diff",
        description='Calculate difference between two vectors (v1-v2)'
    )

    parser.add_argument(
        'vector1',
        help='First vector (/array, e.g. InsulationIndex)'
    )

    parser.add_argument(
        'vector2',
        help='Second vector (/array, e.g. InsulationIndex)'
    )

    parser.add_argument(
        'output',
        help='Output VectorDifference file.'
    )

    parser.add_argument(
        '-a', '--absolute', dest='absolute',
        action='store_true',
        help='''Output absolute difference'''
    )
    parser.set_defaults(absolute=False)
    return parser


def diff(argv):
    parser = diff_parser()

    args = parser.parse_args(argv[2:])

    import os.path
    import kaic

    v1 = kaic.load(args.vector1, mode='r')
    v2 = kaic.load(args.vector2, mode='r')

    output_file = os.path.expanduser(args.output)

    with kaic.VectorDifference(v1, v2, absolute=args.absolute, file_name=output_file, mode='w') as d:
        d.calculate()


def stats_parser():
    parser = argparse.ArgumentParser(
        prog="kaic stats",
        description='Get statistics on number of reads used at each step of a pipeline.'
    )

    parser.add_argument(
        'output',
        help="Output file (.txt) to store statistics."
    )

    parser.add_argument(
        '-f', '--fastq', dest='fastq',
        nargs='+',
        help='''List of FASTQ files or folders containing FASTQ files.'''
    )

    parser.add_argument(
        '-r', '--reads', dest='reads',
        nargs='+',
        help='''List of Reads files or folders containing Reads files ('.reads ending').'''
    )

    parser.add_argument(
        '-p', '--pairs', dest='pairs',
        nargs='+',
        help='''List of Pairs files or folders containing Pairs files ('.pairs ending').'''
    )

    parser.add_argument(
        '-c', '--hic', dest='hic',
        nargs='+',
        help='''List of Hic files or folders containing Hic files ('.hic ending').'''
    )
    return parser


def stats(argv):
    parser = stats_parser()

    args = parser.parse_args(argv[2:])

    from collections import defaultdict

    output_file = os.path.expanduser(args.output)
    with open(output_file, 'w') as o:
        o.write("type\tfile\tproperty\tcount\n")

    def get_files(paths, endings=()):
        files = []
        for p in paths:
            ppath = os.path.expanduser(p)
            if os.path.isdir(ppath):
                for path in os.listdir(ppath):
                    full_path = ppath + '/{}'.format(path)
                    if os.path.isfile(full_path):
                        for ending in endings:
                            if path.endswith(ending):
                                files.append(full_path)
                                continue
            elif os.path.isfile(ppath):
                files.append(ppath)
        return files

    def stats(maskable, masked_table):
        import tables as t
        statistics = maskable.mask_statistics(masked_table)

        # calculate total
        if isinstance(masked_table, t.Group):
            total = 0
            for table in masked_table:
                total += table._original_len()
        else:
            total = masked_table._original_len()
        return statistics, total

    # 1. Count number of reads
    if args.fastq is not None:
        logger.info("Processing FASTQ files.")
        import gzip

        def blocks(files, size=65536):
            while True:
                b = files.read(size)
                if not b:
                    break
                yield b

        fastq_files = get_files(args.fastq, ('.fq', '.fastq', '.fq.gz', '.fastq.gz'))

        total_count = 0
        for fastq_file in fastq_files:
            logger.info("{}".format(fastq_file))
            if fastq_file.endswith('gz'):
                read = gzip.open
            else:
                read = open

            with read(fastq_file, 'r') as f:
                line_count = sum(bl.count("\n") for bl in blocks(f))
            total_count += line_count/4

            with open(output_file, 'a') as o:
                o.write("fastq\t{}\tcount\t{}\n".format(fastq_file, line_count/4))

        with open(output_file, 'a') as o:
            o.write("fastq\ttotal\tcount\t{}\n".format(total_count))

    # 2. Reads statistics
    if args.reads is not None:
        logger.info("Processing Reads files.")

        from kaic.construct.seq import Reads
        reads_files = get_files(args.reads, ('.reads',))

        reads_summary = defaultdict(int)
        for reads_file in reads_files:
            logger.info("{}".format(reads_file))
            reads = Reads(reads_file, mode='r')
            statistics, total = stats(reads, reads._reads)

            with open(output_file, 'a') as o:
                for key in sorted(statistics.keys()):
                    o.write("reads\t{}\t{}\t{}\n".format(reads_file, key, statistics[key]))
                    reads_summary[key] += statistics[key]

            with open(output_file, 'a') as o:
                o.write("reads\t{}\ttotal\t{}\n".format(reads_file, total))
                reads_summary['total'] += total
            reads_summary['filtered'] += total - statistics['unmasked']
            reads_summary['remaining'] += statistics['unmasked']

        with open(output_file, 'a') as o:
            for key in sorted(reads_summary.keys()):
                if key != 'filtered' and key != 'remaining':
                    o.write("reads\ttotal\t{}\t{}\n".format(key, reads_summary[key]))
            o.write("reads\ttotal\tfiltered\t{}\n".format(reads_summary['filtered']))
            o.write("reads\ttotal\tremaining\t{}\n".format(reads_summary['remaining']))

    # 3. Pairs statistics
    if args.pairs is not None:
        logger.info("Processing Pairs files.")
        import kaic
        pairs_files = get_files(args.pairs, ('.pairs',))
        pairs_summary = defaultdict(int)
        for pairs_file in pairs_files:
            logger.info("{}".format(pairs_file))
            pairs = kaic.Pairs(pairs_file, mode='r')
            statistics, total = stats(pairs, pairs._pairs)

            with open(output_file, 'a') as o:
                for key in sorted(statistics.keys()):
                    o.write("pairs\t{}\t{}\t{}\n".format(pairs_file, key, statistics[key]))
                    pairs_summary[key] += statistics[key]

            with open(output_file, 'a') as o:
                o.write("pairs\t{}\ttotal\t{}\n".format(pairs_file, total))
                pairs_summary['total'] += total

            pairs_summary['filtered'] += total - statistics['unmasked']
            pairs_summary['remaining'] += statistics['unmasked']

        with open(output_file, 'a') as o:
            for key in sorted(pairs_summary.keys()):
                if key != 'filtered' and key != 'remaining':
                    o.write("pairs\ttotal\t{}\t{}\n".format(key, pairs_summary[key]))
            o.write("pairs\ttotal\tfiltered\t{}\n".format(pairs_summary['filtered']))
            o.write("pairs\ttotal\tremaining\t{}\n".format(pairs_summary['remaining']))

    # 3. Hic statistics
    if args.hic is not None:
        logger.info("Processing Hic files.")
        from kaic.data.genomic import load_hic
        hic_files = get_files(args.hic, ('.hic',))

        hic_summary = defaultdict(int)
        for hic_file in hic_files:
            logger.info("{}".format(hic_file))
            hic = load_hic(hic_file, mode='r')
            statistics, total = stats(hic, hic._edges)

            with open(output_file, 'a') as o:
                for key in sorted(statistics.keys()):
                    o.write("hic\t{}\t{}\t{}\n".format(hic_file, key, statistics[key]))
                    hic_summary[key] += statistics[key]

            with open(output_file, 'a') as o:
                o.write("hic\t{}\ttotal\t{}\n".format(hic_file, total))
                hic_summary['total'] = total

            hic_summary['filtered'] += total - statistics['unmasked']
            hic_summary['remaining'] += statistics['unmasked']

        with open(output_file, 'a') as o:
            for key in sorted(hic_summary.keys()):
                if key != 'filtered' and key != 'remaining':
                    o.write("hic\ttotal\t{}\t{}\n".format(key, hic_summary[key]))
            o.write("hic\ttotal\tfiltered\t{}\n".format(hic_summary['filtered']))
            o.write("hic\ttotal\tremaining\t{}\n".format(hic_summary['remaining']))


def write_config_parser():
    parser = argparse.ArgumentParser(
        prog="kaic write_config",
        description='Write default config file to specified location.'
    )

    parser.add_argument(
        'config_file',
        help="Output file for default configuration."
    )

    parser.add_argument(
        '-f', '--force', dest='force',
        action='store_true',
        help='''Force overwrite of existing config file.'''
    )
    parser.set_defaults(force=False)
    return parser


def write_config(argv):
    parser = write_config_parser()

    args = parser.parse_args(argv[2:])

    from kaic.config import write_default_config
    write_default_config(os.path.expanduser(args.config_file), overwrite=args.force)

