'''
Created on May 20, 2015

@author: kkruse1
'''

import tables as t
import os.path
from xml.etree.ElementTree import iterparse, ParseError
import string
import random
import h5py

def without_extension(file_name):
    os.path.splitext(file_name)[0]

def get_extension(file_name):
    os.path.splitext(file_name)[1][1:]


def make_dir(dir_name, fail_if_exists=False, make_subdirs=True):
    if make_subdirs:
        f = os.makedirs
    else:
        f = os.mkdir
        
    try: 
        f(dir_name)
    except OSError:
        if not fail_if_exists and not os.path.isdir(dir_name):
            raise

def get_number_of_lines(file_name):
    with open(file_name,'r') as f:
        n = sum(1 for line in f)  # @UnusedVariable
    return n

def random_name(length=6):
    return ''.join(random.SystemRandom().choice(string.uppercase + string.digits) for _ in xrange(length))  # @UndefinedVariable
        

def create_or_open_pytables_file(file_name=None, inMemory=False, mode='a'):
    if file_name is None:
        file_name = random_name()
    
    mem = 0 if inMemory else 1
    
    # check if is existing
    if os.path.isfile(file_name):
        try:
            f = t.open_file(file_name, "r", driver="H5FD_CORE",driver_core_backing_store=mem)
            f.close()
        except t.HDF5ExtError:
            raise ImportError("File exists and is not an HDF5 dict")
        
    return t.open_file(file_name, mode, driver="H5FD_CORE",driver_core_backing_store=mem)


def is_bed_file(file_name):
    if not file_name:
        return False
    
    def is_bed_line(line):
        l = line.rstrip().split("\t")
        if len(l) > 2:
            try:
                int(l[1])
                int(l[2])
            except ValueError:
                return False
        else:
            return False
        return True
        
    with open(file_name, 'r') as f:
        if not is_bed_line(f.readline()):
            return is_bed_line(f.readline())
        else:
            return True
        
def is_bedpe_file(file_name):
    if not file_name:
        return False
    
    def is_bedpe_line(line):
        l = line.rstrip().split("\t")
        if len(l) > 5:
            try:
                int(l[1])
                int(l[2])
                int(l[4])
                int(l[5])
            except ValueError:
                return False
        else:
            return False
        return True
        
    with open(file_name, 'r') as f:
        if not is_bedpe_line(f.readline()):
            return is_bedpe_line(f.readline())
        else:
            return True
        
        
def is_hic_xml_file(file_name):
    try:
        for event, elem in iterparse(file_name):  # @UnusedVariable
            if elem.tag == 'hic':
                return True
            elem.clear()
    except ParseError:
        return False
    
    return False

def is_hdf5_file(file_name):
    try:
        f = h5py.File(file_name,'r')
        f.close()
    except IOError:
        return False
    return True
    
    