import sys
import numpy as np
from collections import defaultdict
import time
from os.path import join
from basic_hasher import build_hash_and_pickle, hashing_algorithm
from helpers.helpers import *

READ_LENGTH = 50


def generate_pileup(aligned_fn):
    """
    :param aligned_fn: The filename of the saved output of the basic aligner
    :return: SNPs (the called SNPs for uploading to the herokuapp server)
             output_lines (the reference, reads, consensus string, and diff string to be printed)
    """
    line_count = 0
    lines_to_process = []
    changes = []
    start = time.clock()
    with open(aligned_fn, 'r') as input_file:
        for line in input_file:
            line_count += 1
            line = line.strip()
            if line_count <= 4 or line == '':  # The first 4 lines need to be skipped
                continue
            if len(line) > 0 and all(x == '-' for x in line):  # The different pieces of the genome are set off
                # with lines of all dashes '--------'
                new_changes = process_lines(lines_to_process)
                lines_to_process = []
                changes += new_changes
                # print time.clock() - start, 'seconds'
            else:
                lines_to_process.append(line)
    snps = [v for v in changes if v[0] == 'SNP']
    snpsCleaned = []
    for i in range(1, len(snps)):
        if (snps[i-1][3]+10<snps[i][3]):
            snpsCleaned.append(snps[i])
    insertions = [v for v in changes if v[0] == 'INS']
    deletions = [v for v in changes if v[0] == 'DEL']
    return snpsCleaned, insertions, deletions


def process_lines(genome_lines):
    """
    :param genome_lines: Lines in between dashes from the saved output of the basic_aligner
    :return: snps (the snps from this set of lines)
             output_lines (the lines to print, given this set of lines)
    """
    line_count = 0
    consensus_lines = []
    for line in genome_lines:
        line_count += 1
        if line_count == 1:  # The first line contains the position in the reference where the reads start.
            raw_index = line.split(':')[1]
            line_index = int(raw_index)
        else:
            consensus_lines.append(line[6:])
    ref = consensus_lines[0]
    aligned_reads = consensus_lines[1:]
    donor = generate_donor(ref, aligned_reads)
    changes = identify_changes(ref, donor, line_index)
    n_indels = sum([1 if changes[i] == 'INS' or changes[i] == 'DEL' else 0 for i in range(len(changes))])
    if (n_indels == 0):
        changes = identify_changes(ref, consensus(ref, aligned_reads), line_index)
    return changes


def align_to_donor(donor, read):
    """
    :param donor: Donor genome (a character string of A's, T's, C's, and G's, and spaces to represent unknown bases).
    :param read: A single read padded with spaces
    :return: The best scoring
    """

    mismatches = [1 if donor[i] != ' ' and read[i] != ' ' and
                       read[i] != donor[i] else 0 for i in range(len(donor))]
    n_mismatches = sum(mismatches)
    overlaps = [1 if donor[i] != ' ' and read[i] != ' ' else 0 for i in range(len(donor))]
    n_overlaps = sum(overlaps)
    score = n_overlaps - n_mismatches
    if n_mismatches <= 2:
        return read, score
    else:
        best_read = read
        best_score = score

    for shift_amount in range(-3, 0) + range(1, 4):  # This can be improved
        if shift_amount > 0:
            shifted_read = ' ' * shift_amount + read
        elif shift_amount < 0:
            shifted_read = read[-shift_amount:] + ' ' * (-shift_amount)
        mismatches = [1 if donor[i] != ' ' and shifted_read[i] != ' ' and
                           shifted_read[i] != donor[i] else 0 for i in range(len(donor))]
        n_mismatches = sum(mismatches)
        overlaps = [1 if donor[i] != ' ' and shifted_read[i] != ' ' else 0 for i in range(len(donor))]
        n_overlaps = sum(overlaps)
        score = n_overlaps - 1.5*n_mismatches - 3.5 * abs(shift_amount)
        if score > best_score:
            best_read = shifted_read
            best_score = score
    return best_read, best_score


def generate_donor(ref, aligned_reads):
    """
    Aligns the reads against *each other* to generate a hypothesized donor genome.
    There are lots of opportunities to improve this function.
    :param aligned_reads: reads aligned to the genome (with pre-pended spaces to offset correctly)
    :return: hypothesized donor genome
    """
    cleaned_aligned_reads = [_.replace('.', ' ') for _ in aligned_reads]
    ## Start by appending spaces to the reads so they line up with the reference correctly.
    padded_reads = [aligned_read + ' ' * (len(ref) - len(aligned_read)) for aligned_read in cleaned_aligned_reads]
    consensus_string = consensus(ref, aligned_reads)

    ## Seed the donor by choosing the read that best aligns to the reference.
    read_scores = [sum([1 if padded_read[i] == ref[i] and padded_read[i] != ' '
                        else 0 for i in range(len(padded_read))])
                   for padded_read in padded_reads]
    if not read_scores:
        return consensus_string

    #try to get a better seed by using the longest matching subsequence of consensus
    consensus_seed_scores = [sum([1 if consensus_string[i+d] == ref[i+d] and consensus_string[i+d] != ' '
                        else 0 for i in range(0,50)])
                   for d in range(0,50)]

    #EDIT: if there's a really good match (49/50 or 50/50) from the consensus, use that as the seed
    longest_read = ""
    if not consensus_seed_scores:
        if (max(consensus_seed_scores) >= 49):
            longest_read = consensus_string[consensus_seed_scores.index(max(consensus_seed_scores)):]
        else:
            return ref #EDIT: if there's not a good match on the consensus, just bail
    else:
        longest_read = padded_reads[read_scores.index(max(read_scores))]
    donor_genome = longest_read

    # While there are reads that haven't been aligned, try to align them to the donor.
    while padded_reads:
        un_donored_reads = []
        for padded_read in padded_reads:
            re_aligned_read, score = align_to_donor(donor_genome, padded_read)
            if score < 15:  # If the alignment isn't good, throw the read back in the set of reads to be aligned.
                un_donored_reads.append(padded_read)
            else:
                donor_genome = ''.join([re_aligned_read[i] if donor_genome[i] == ' ' else donor_genome[i]
                                        for i in range(len(donor_genome))])

        if len(un_donored_reads) == len(padded_reads):
            # If we can't find good alignments for the remaining reads, quit
            break
        else:
            # Otherwise, restart the alignment with a smaller set of unaligned reads
            padded_reads = un_donored_reads

    ## Fill in any gaps with the consensus sequence and return the donor genome.
    donor_genome = ''.join([donor_genome[i] if donor_genome[i] != ' ' else consensus_string[i] for i
                            in range(len(donor_genome))])

    return donor_genome


def edit_distance_matrix(ref, donor):
    """
    Computes the edit distance matrix between the donor and reference
    This algorithm makes substitutions, insertions, and deletions all equal.
    Does that strike you as making biological sense? You might try changing the cost of
    deletions and insertions vs snps.
    :param ref: reference genome (as an ACTG string)
    :param donor: donor genome guess (as an ACTG string)
    :return: complete (len(ref) + 1) x (len(donor) + 1) matrix computing all changes
    """

    output_matrix = np.zeros((len(ref), len(donor)))
    # print len(ref), len(donor)
    # print output_matrix
    # This is a very fast and memory-efficient way to allocate a matrix
    for i in range(len(ref)):
        output_matrix[i, 0] = i
    for j in range(len(donor)):
        output_matrix[0, j] = j
    for j in range(1, len(donor)):
        for i in range(1, len(ref)):  # Big opportunities for improvement right here.
            deletion = output_matrix[i - 1, j] + 1
            insertion = output_matrix[i, j - 1] + 1
            identity = output_matrix[i - 1, j - 1] if ref[i] == donor[j] else np.inf
            substitution = output_matrix[i - 1, j - 1] + 1 if ref[i] != donor[j] else np.inf
            output_matrix[i, j] = min(insertion, deletion, identity, substitution)
    return output_matrix


def identify_changes(ref, donor, offset):
    """
    Performs a backtrace-based re-alignment of the donor to the reference and identifies
    SNPS, Insertions, and Deletions.
    Note that if you let either sequence get too large (more than a few thousand), you will
    run into memory issues.
    :param ref: reference sequence (ATCG string)
    :param donor: donor sequence (ATCG string)
    :param offset: The starting location in the genome.
    :return: SNPs, Inserstions, and Deletions
    """
    # print offset
    ref = '${}'.format(ref)
    donor = '${}'.format(donor)
    edit_matrix = edit_distance_matrix(ref=ref, donor=donor)
    current_row = len(ref) - 1
    current_column = len(donor) - 1
    changes = []

    n_ins = 0
    n_del = 0
    n_snp = 0

    while current_row > 0 or current_column > 0:
        if current_row == 0:
            pvs_row = -np.inf
        else:
            pvs_row = current_row - 1

        if current_column == 0:
            pvs_column = -np.inf
        else:
            pvs_column = current_column - 1

        try:
            insertion_dist = edit_matrix[current_row, pvs_column]
        except IndexError:
            insertion_dist = np.inf

        try:
            deletion_dist = edit_matrix[pvs_row, current_column]
        except IndexError:
            deletion_dist = np.inf

        try:
            if ref[current_row] == donor[current_column]:
                identity_dist = edit_matrix[pvs_row, pvs_column]
            else:
                identity_dist = np.inf

            if ref[current_row] != donor[current_column]:
                substitution_dist = edit_matrix[pvs_row, pvs_column]
            else:
                substitution_dist = np.inf
        except (TypeError, IndexError) as e:
            identity_dist = np.inf
            substitution_dist = np.inf

        min_dist = min(insertion_dist, deletion_dist, identity_dist, substitution_dist)

        ref_index = current_row + offset - 1
        if min_dist == identity_dist:
            current_row = pvs_row
            current_column = pvs_column
        elif min_dist == substitution_dist:
            changes.append(['SNP', ref[current_row], donor[current_column], ref_index])
            current_row = pvs_row
            current_column = pvs_column
            n_snp += 1
        elif min_dist == insertion_dist:
            if len(changes) > 0 and changes[-1][0] == 'INS' and changes[-1][-1] == ref_index + 1:
                changes[-1][1] = donor[current_column] + changes[-1][1]
                #EDIT: toss out long ins
                if (len (changes[-1][1]) > 5):
                    return []
            else:
                changes.append(['INS', donor[current_column], ref_index + 1])
                n_ins += 1
            current_column = pvs_column
        elif min_dist == deletion_dist:
            if len(changes) > 0 and changes[-1][0] == 'DEL' and changes[-1][-1] == ref_index + 1:
                changes[-1] = ['DEL', ref[current_row] + changes[-1][1], ref_index]
                #EDIT: toss out long dels
                if (len (changes[-1][1]) > 5):
                    return []
            else:
                changes.append(['DEL', ref[current_row], ref_index])
                n_del +=1
            current_row = pvs_row
        else:
            raise ValueError
    changes = sorted(changes, key=lambda change: change[-1])

    #EDIT: toss if too many changes in a 100 block
    if (len(changes) > 3):
         return []

    #EDIT: if 2 SNPs in a row, then bail
    for k in range(1, len(changes)):
        if (changes[k][0]=='SNP' and changes[k-1][0]=='SNP' and changes[k][3]==changes[k-1][3]):
            return []


    print str(changes)
    return changes


def consensus(ref, aligned_reads):
    """
    Identifies a consensus sequence by calling the most commmon base at each location
    in the reference.
    :param ref: reference string
    :param aligned_reads: the list of reads.
    :return: The most common base found at each position in the reads (i.e. the consensus string)
    """
    consensus_string = ''
    padded_reads = [aligned_read + ' ' * (len(ref) - len(aligned_read)) for aligned_read in aligned_reads]
    # The reads are padded with spaces so they are equal in length to the reference
    for i in range(len(ref)):
        base_count = defaultdict(float)
        ref_base = ref[i]
        base_count[ref_base] += 1.1  # If we only have a single read covering a region, we favor the reference.
        read_bases = [padded_read[i] for padded_read in padded_reads if padded_read[i] not in '. ']
        # Spaces and dots (representing the distance between paired ends) do not count as DNA bases
        for base in read_bases:
            base_count[base] += 1
        consensus_base = max(base_count.iterkeys(), key=(lambda key: base_count[key]))
        # The above line chooses (a) key with maximum value in the read_bases dictionary.
        consensus_string += consensus_base
    return consensus_string


if __name__ == "__main__":
    #genome_name = 'practice_W_3'
    genome_name = 'hw2undergrad_E_2'
    input_folder = './{}'.format(genome_name)
    chr_name = '{}_chr_1'.format(genome_name)

    ##COMMENT BLOCK TO REALIGN--------
    # reads_fn_end = 'reads_{}.txt'.format(chr_name)
    # reads_fn = join(input_folder, reads_fn_end)
    # ref_fn_end = 'ref_{}.txt'.format(chr_name)
    # ref_fn = join(input_folder, ref_fn_end)
    # key_length = 8
    # start = time.clock()
    # reads = read_reads(reads_fn)
    # # If you want to speed it up, cut down the number of reads by
    # # changing the line to reads = read_reads(reads_fn)[:<x>] where <x>
    # # is the number of reads you want to work with.
    # genome_hash_table = build_hash_and_pickle(ref_fn, key_length)
    # reference = read_reference(ref_fn)
    # genome_aligned_reads, alignments = hashing_algorithm(reads, genome_hash_table)
    # # print genome_aligned_reads
    # # print alignments
    # output_str = pretty_print_aligned_reads_with_ref(genome_aligned_reads, alignments, reference)
    # # print output_str[:5000]
    #
    # output_fn = join(input_folder, 'aligned_reads_{}.txt'.format(chr_name))
    # with(open(output_fn, 'w')) as output_file:
    #     output_file.write(output_str)
    ###COMMENT BLOCK TO REALIGN--------

    input_fn = join(input_folder, 'aligned_reads_{}.txt'.format(chr_name))
    snps, insertions, deletions = generate_pileup(input_fn)
    output_fn = join(input_folder, 'changes_{}.txt'.format(chr_name))
    with open(output_fn, 'w') as output_file:
        output_file.write('>' + chr_name + '\n>SNP\n')
        for x in snps:
            output_file.write(','.join([str(u) for u in x[1:]]) + '\n')
        output_file.write('>INS\n')
        for x in insertions:
            output_file.write(','.join([str(u) for u in x[1:]]) + '\n')
        output_file.write('>DEL\n')
        for x in deletions:
            output_file.write(','.join([str(u) for u in x[1:]]) + '\n')


    identify_changes(ref='ACACCC', donor='ATACCCGGG', offset=0)
    identify_changes(ref='ATACCCGGG', donor='ACACCC', offset=0)
    identify_changes(ref='ACACCC', donor='GGGATACCC', offset=0)
    identify_changes(ref='ACA', donor='AGA', offset=0)
    identify_changes(ref='ACA', donor='ACGTA', offset=0)
    identify_changes(ref='TTACCGTGCAAGCG', donor='GCACCCAAGTTCG', offset=0)