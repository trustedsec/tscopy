"""
This project is based off the work from the following projects:
* https://github.com/williballenthin/python-ntfs 
* https://github.com/jschicht/RawCopy
"""
# TODO: Will have issues with non ascii characters in files names
# TODO: Currently only processes '\\.\' where RawCopy supported other formats
# TODO: Parsing of command line input for multiple files needs to be more robust
import logging
import sys
import os
import pickle
import argparse
import time
import traceback
import struct
try:
    import win32file, win32api, win32con
except:
    print "Must have pywin32 installed -- pip install pywin32"
    sys.exit(1)

from math import ceil
from BinaryParser import Mmap, hex_dump, Block
from MFT import INDXException, MFTRecord, Attribute, ATTR_TYPE 
from MFT import StandardInformation,FilenameAttribute, INDEX_ROOT

g_logger = logging.getLogger("ntfs.examples.inspect_record")

class BootSector(Block):
    def __init__(self, buf, offset):
        super(BootSector, self).__init__(buf, offset)
        self.declare_field("qword", "system_id", 0x3)
        self.declare_field("word", "bytes_per_sector", 0x0b)
        self.declare_field("byte", "sectors_per_cluster", 0xd)
        self.declare_field("word", "reserved_sectors", 0xe)
        self.declare_field("byte", "media_desc", 0x15)
        self.declare_field("word", "sectors_per_track", 0x18)
        self.declare_field("word", "heads", 0x1a)
        self.declare_field("dword", "hidden_sectors", 0x1c)
        self.declare_field("qword", "total_sectors", 0x28)
        self.declare_field("qword", "start_c_mft", 0x30)
        self.declare_field("qword", "start_c_mftmir", 0x38)
        self.declare_field("byte", "file_rec_indicator", 0x40)
        self.declare_field("byte", "idx_buf_size_indicator", 0x44)
        self.declare_field("qword", "serial_number", 0x48)
        self.bytes_per_cluster = self.bytes_per_sector() * self.sectors_per_cluster()
        #COPIED FROM  RAWCOPY:: A really lame fix for a rare bug seen in certain Windows 7 x64 vm's
        if self.file_rec_indicator() > 127:
            testval = 256 - self.file_rec_indicator()
            self.mft_record_size = 2
            for i in range(testval-1):
                self.mft_record_size *= 2
        else:
            self.mft_record_size = self.bytes_per_cluster * self.file_rec_indicator()
            
        self.sectors_per_mft_record = self.mft_record_size / self.bytes_per_sector()
        self.cluster_per_file_record_segment = int(ceil(float(self.mft_record_size) / self.bytes_per_cluster))
        

class INDX( Block ):
    def __init__(self, buf, offset):
        super(INDX, self).__init__(buf, offset)
        self.declare_field("dword", "magic", 0x0)
        self.declare_field("word", "update_seq_offset", 0x4)
        self.declare_field("word", "update_seq_sz", 0x6)
        self.declare_field("qword", "logfile_seq_num", 0x8)
        self.declare_field("qword", "VCN_INDX", 0x10)
        self.declare_field("dword", "index_entries_offset", 0x18)
        self.declare_field("dword", "index_entries_sz", 0x1c)
        self.declare_field("dword", "alloc_sz", 0x20)
        self.declare_field("byte", "leaf_node", 0x24)
        self.declare_field("word", "update_seq", 0x28)
        s = self.update_seq_sz()
    def update_seq_arr( self, idx_buf ):
        # TODO: Clean this up into a for loop
        seq_arr = idx_buf[self.update_seq_offset()+2:self.update_seq_offset()+2+self.update_seq_sz()*2]
#        logging.debug(hex_dump(seq_arr))
        ret  = idx_buf[0x0000:0x01fe] + seq_arr[0x00:0x2]
        ret += idx_buf[0x0200:0x03fe] + seq_arr[0x02:0x4]
        ret += idx_buf[0x0400:0x05fe] + seq_arr[0x04:0x6]
        ret += idx_buf[0x0600:0x07fe] + seq_arr[0x06:0x8]
        ret += idx_buf[0x0800:0x09fe] + seq_arr[0x08:0xa]
        ret += idx_buf[0x0a00:0x0bfe] + seq_arr[0x0a:0xc]
        ret += idx_buf[0x0c00:0x0dfe] + seq_arr[0x0c:0xe]
        ret += idx_buf[0x0e00:0x0ffe] + seq_arr[0x0e:0x10]
        ret += idx_buf[0x1000:      ] 
        return ret

class INDX_ENTRY( Block ):
    def __init__(self, buf, offset):
        super(INDX_ENTRY, self).__init__(buf, offset)
        self.declare_field("qword", "mft_recordnum", 0)
        self.declare_field("word", "entry_sz", 0x08 )
        if self.entry_sz() == 0x10 and self.mft_recordnum() == 0:
            raise INDXException("End of INDX File found")
        if self.entry_sz() == 0x00 and self.mft_recordnum() == 0:
            raise INDXException("NULLS INDX File found")
        self.declare_field("word", "filename_offset", 0x0a )
        self.declare_field("word", "index_flags", 0x0c )
        self.declare_field("qword", "mft_parent_recordnum", 0x10 )
        self.declare_field("qword", "alloc_sz", 0x38 )
        self.declare_field("qword", "file_sz", 0x40 )
        self.declare_field("qword", "file_flags", 0x48 )
        self.declare_field("byte", "filename_sz", 0x50 )
        self.declare_field("binary", "filename", 0x52, self.filename_sz()*2 )

class TScopy( object ):
    def __init__( self ):
        self.config = { 'files': None,
                        'outputdir': None,
                        'debug': True,
                        'ignore_table':None
                      }
        self.__pickle_fullpath = None
        self.__pickle_filename = 'mft.pickle'

    def setConfig( self, config ):
#        self.config = config
        if config['is_dir'] == True:
            self.setDirectory( config['files'] )
        else:
            self.config['files'] = config['files']
        self.setDebug( config['debug'] )
        self.setLookupTable( config['ignore_table'] )
        self.setOutputDir( config['outputdir'] )

    def setDirectory( self, directory ):
        tmp_dir = directory
        if tmp_dir[-1] == os.sep:
            tmp_dir = tmp_dir[:-1]
        if not os.path.isdir( args.directory ):
            print "Error directory (%s) not found" % tmp_dir
            parser.print_help()
            raise Exception( "TSCOPY", "Error directory (%s) not found" % tmp_dir)
        process_files = []
        for i in os.listdir( tmp_dir):
            if not os.path.isdir(  tmp_dir + os.sep + i ):
                process_files.append( tmp_dir + os.sep + i )
        self.config['files'] = process_files

    def setDebug( self, debug ):
        self.config['debug'] = debug

    def setLookupTable( self, tf ):
        self.config['ignore_table'] = tf

    def setOutputDir( self, directory ):
        if not os.path.isdir( directory ):
            logging.error("Error output destination (%s) not found" % directory)
            parser.print_help()
            raise Exception( "TSCOPY", "Error output destination (%s) not found" % directory)
        self.config['outputdir'] = directory 
        self.__pickle_fullpath = '%s%s%s' % ( directory, os.sep, self.__pickle_filename )
                
    def __getMFT( self, index=0 ):
        fd = self.config['fd']
        bss = self.config['bss']
        mft_offset = bss.bytes_per_sector() * bss.sectors_per_cluster() * bss.start_c_mft()
        win32file.SetFilePointer( fd, mft_offset+(index*0x400), win32file.FILE_BEGIN)
        buf = win32file.ReadFile( fd, 0x400)[1]
        record = MFTRecord(buf, 0, None)
        ret = {}

        attribute = record.data_attribute()
        cnt = 0
        for offset, length in attribute.runlist().runs():
            if length > 16 and (length%16) > 0:
                if offset == 0:
                     # may be sparse section at end of Compression Signature
                     ret[cnt] = (offset, length%16)
                     length -= length%16
                     cnt += 1
                else:
                     #may be compressed data section at start of Compression Signature
                     ret[cnt] = (offset, length-length%16)
                     offset += length-length%16
                     length = length%16
                     cnt += 1
            #just normal or sparse data
            ret[cnt] = (offset, length)
            cnt += 1
        
        return ret

    def __GenRefArray( self ):
        MFTClustersToKeep = 0
        ref = -1
        dataruns = self.config['mft_dataruns']
        bytes_per_cluster = self.config['bss'].bytes_per_cluster 
        ClustersPerFileRecordSegment = self.config['bss'].cluster_per_file_record_segment 
        split_mft_rec = {} 
        cnt = 0
        for x in dataruns:
            r = dataruns[x]
            doKeepCluster = MFTClustersToKeep
            MFTClustersToKeep = (r[1]+ClustersPerFileRecordSegment - MFTClustersToKeep) % ClustersPerFileRecordSegment
            if not MFTClustersToKeep == 0:
                MFTClustersToKeep = ClustersPerFileRecordSegment - MFTClustersToKeep
            pos = r[0] * bytes_per_cluster 
            subtr = self.config['bss'].mft_record_size 
            if  MFTClustersToKeep or doKeepCluster:
                subtr = 0
            end_of_run = r[1] * bytes_per_cluster - subtr
            for i in range(0, end_of_run, self.config['bss'].mft_record_size):
                if MFTClustersToKeep:
                    if i >= end_of_run - ((ClustersPerFileRecordSegment - MFTClustersToKeep) * bytes_per_cluster):
                        bytesToGet = (ClustersPerFileRecordSegment - MFTClustersToKeep) * bytes_per_cluster
                        split_mft_rec[cnt] = '%d?%d,%d' % (ref+1, pos+i, bytesToGet )
                ref += 1
                if i == 0 and doKeepCluster:
                    bytesToGet = doKeepCluster * bytes_per_cluster
                    if bytesToGet > self.config['bss'].mft_record_size:
                        bytesToGet = self.config['bss'].mft_record_size 
                    split_mft_rec[cnt] += '|%d&%d' % ( pos+i, bytesToGet )
                cnt += 1
        self.config['split_mft_rec'] = split_mft_rec
            
    def __isSplitMFT( self, array, target_seq_num ):
        for ind in array:
            i = array[ind]
            if not '?' in i:
                continue
            ind = i.index('?')
            testRef = i[0:ind]   
            if int(testRef) == target_seq_num:
                return ind 
        return None

    def __calcOffset( self, target_seq_num ):
        fd = self.config['fd']
        bss = self.config['bss']
        mft_vcn = self.config['mft_dataruns']
        image_offset = 0 # TODO: Change this when finished processing the image
        array = self.config['split_mft_rec']

        # Handle in the case that the object is split accross two dataruns
        split = self.__isSplitMFT( array, target_seq_num )
        if not split == None:
            logging.debug( 'calcOffset: a split record was detected' )
            item = array[split]
            ind = item.index('?')
            testRef = item[0:ind]   
            if not int(testRef) == target_seq_num:
                logging.debug("Error: The ref in the array did not match target ref.")
                return None
            
            srecord3 = item[ind+1:]
            srecordArr = srecord3.split('|')
            if not len( srecordArr ) == 3:
                logging.debug("Error: Array contained more elements than expected: %d" % len( srecordArr ))
                return None

            record = ""
            for i in srecordArr:
                if not ',' in i: 
                    logging.debug('Split:: Could not find ","')
                    continue
                ind = i.index(',')
                srOffset = i[:ind]
                srSize   = i[ind+1:]
                win32file.SetFilePointer( fd, srOffset + image_offset, win32file.FILE_BEGIN)
                record += win32file.ReadFile( fd, srSize)[1]
            return record
        else:
            counter = 0
            offset = 0
            recordsdivisor = bss.mft_record_size/512
            for indx in mft_vcn: 
                current_cluster = mft_vcn[indx][1]
                offset = mft_vcn[indx][0]
                records_in_currentrun = (current_cluster * bss.sectors_per_cluster() ) / recordsdivisor 
                counter += records_in_currentrun 
                if counter > target_seq_num:
                    break
            tryat = counter - records_in_currentrun
            records_per_cluster = bss.sectors_per_cluster() / recordsdivisor
            final = 0
            counter2 = 0
            record_jmp = 0
            while final < target_seq_num:
                record_jmp += records_per_cluster
                counter2 += 1
                final = tryat + record_jmp
            records_to_much = final - target_seq_num

            mft_offset = image_offset + offset * bss.bytes_per_cluster + ( counter2 * bss.bytes_per_cluster ) - ( records_to_much * bss.mft_record_size )
            win32file.SetFilePointer( fd, mft_offset, win32file.FILE_BEGIN)
            return win32file.ReadFile( fd, 0x400)[1]
        return None

    def __getChildIndex( self, index  ):
        fd = self.config['fd']
        bss = self.config['bss']
        bpc = bss.bytes_per_cluster

        buf = self.__calcOffset( index )
        if buf == None:
            raise Exception("Failed to process mft_offset")
        record = MFTRecord(buf, 0, None)
        if not record.is_directory():
            return []
        ret  = []
        for attribute in record.attributes():
            if attribute.type() == ATTR_TYPE.INDEX_ROOT:
                for entry in INDEX_ROOT(attribute.value(), 0).index().entries():
                    ret.append((entry.header().mft_reference() & 0xfffffffff, entry.filename_information().filename())  )
            if attribute.type() == ATTR_TYPE.INDEX_ALLOCATION:
                for cluster_offset, length  in attribute.runlist().runs():
                    offset=cluster_offset*bpc
                    win32file.SetFilePointer( fd, offset, win32file.FILE_BEGIN)
                    buf = win32file.ReadFile( fd, length*bpc)[1]
                    for cnt in range(length):
                        idx_buf = buf[cnt*bpc:(cnt+2)*bpc]
#                        logging.debug("BEGIN"+"*"*80)
#                        logging.debug(hex_dump(idx_buf))
                        ind = INDX( idx_buf, 0 )
                        idx_buf = ind.update_seq_arr( idx_buf )
#                        logging.debug("*"*80)
#                        logging.debug(hex_dump(idx_buf))
#                        logging.debug("END"+"*"*80)
                        entry_offset = ind.index_entries_offset()+0x18 
                        i = 0 
                        last_i = i
#                        logging.debug( 'index_entries_sz %04x' % ind.index_entries_sz() )
                        while i < ind.index_entries_sz() :
                            try:
                                entry  = INDX_ENTRY( idx_buf, entry_offset )
                                ret.append( (entry.mft_recordnum(), entry.filename().replace('\x00','')))
#                                logging.debug('i %04x seq_num %016x Filename: %s' % (i,entry.mft_recordnum()&0xffffffff, entry.filename().replace('\x00','')))
                            except   INDXException:
                                break
                            except:
#                                traceback.print_exc()
                                logging.debug(traceback.format_exc())
                                pass
                            entry_offset += entry.entry_sz()

                            i += entry.entry_sz()
                            if entry.entry_sz() == 0:
                                break
        return ret

    def __getFile( self, mft_file_object ):
        fd = self.config['fd']
        bpc = self.config['bss'].bytes_per_cluster

        buf = self.__calcOffset( mft_file_object[0] )

        if buf == None:
            raise Exception("Failed to process mft_offset")
        try:
            record = MFTRecord(buf, 0, None)
            for attribute in record.attributes():
                if attribute.type() == ATTR_TYPE.DATA:
                    fullpath = self.config['outputdir'] + self.config['current_file']
                    logging.debug( "GetFile:: fullpath %s" % fullpath )
                    logging.debug( "GetFile:: attributes %s" % attribute.get_all_string())
                    path = '\\'.join( fullpath.split('\\')[:-1])
                    if not os.path.isdir( path ): 
                        os.makedirs( path )
                    fd2 = open( fullpath,'wb' )
                    
                    try:
                        logging.debug("non_resident %r" % attribute.non_resident() ) 
                        if attribute.non_resident() == 0:
                            fd2.write( attribute.value()) 
                        else:
                            cnt = 0
                            padd = False
                            for cluster_offset, length in attribute.runlist().runs():
    #                            logging.debug("GetFile:: cluster_offset( %08x ) lenght( %08x )  " % ( cluster_offset, length))
                                read_sz = length * bpc 
    #                            logging.debug("readsize %08x cnt %08x init_sz %08x" % ( read_sz, cnt, attribute.initialized_size()))
                                if read_sz + cnt > attribute.initialized_size():
                                    read_sz = attribute.initialized_size() - cnt
                                    padd = True
    #                            logging.debug("readsize %08x cnt %08x init_sz %08x" % ( read_sz, cnt, attribute.initialized_size()))
                                offset=cluster_offset * bpc
                                win32file.SetFilePointer( fd, offset, win32file.FILE_BEGIN)
                                buf = win32file.ReadFile( fd, read_sz)[1]
                                if attribute.data_size() < cnt + read_sz:
                                    read_sz = attribute.data_size()-cnt
    #                            cnt += length * bpc
                                cnt += read_sz
                                fd2.write(buf[:read_sz])
                                if padd == True:
                                    padd_sz  = attribute.data_size() - attribute.initialized_size() 
                                    fd2.write( '\x00' * padd_sz )
                                    cnt += padd_sz
                    except:
                        logging.error('Failed to get file %s' % (mft_file_object[1] ) )
                        logging.debug('Failed to get file %s\n%s' % (mft_file_object[1], traceback.format_exc() ))
                    finally:
                        fd2.close()
        except:
            logging.debug('Failed to get file %s\n%s' % (mft_file_object[1], traceback.format_exc() ))
        
    def __process_image( self, targetDrive ):
        #TODO:: Come back and add this section. It finds the ofsets for the volumen that I hardcoded
        # image_offset is defaulted to zero except for other types of images that we do not support
        pass

    def __getLookupTableFromDisk( self ):
        if not os.path.isfile( self.__pickle_fullpath):
            return {5:{'seq_num': 5, 'name':'','children':{}}}
        with open( filename, 'rb') as fd:
            return pickle.loads( fd.read() )
        
    def __saveLookuptable( self, lookup_table ):
        with open(self.__pickle_fullpath, 'wb') as fd:
            fd.write( pickle.dumps( lookup_table ))

    def __check_config( self ):
        if self.config['files'] == None or self.config['outputdir'] == None:
            return False
        return True    
        
    def copy(self):
        if self.__check_config( ) == False:
            logging.error( 'Missing needed filenames to copy, or the destination' )
            return
            
        if not self.config['files'][0][:4].lower() == '\\\\.\\':
            targetDrive = '\\\\.\\'+self.config['files'][0][:2]
        else:
            targetDrive = self.config['files'][0][:6]
        self.__process_image( targetDrive ) # TODO process this to determin correct offsets
        if self.config['ignore_table'] == True:
            lookup_table = {5:{'seq_num':5,'name':'','children':{}}}
        else:
            lookup_table = self.__getLookupTableFromDisk( )

        if self.config['debug'] == True:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        logging.debug( 'Target Drive %s' % targetDrive )
        fd = win32file.CreateFile( targetDrive,
                                win32file.GENERIC_READ,
                                win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
                                None, 
                                win32con.OPEN_EXISTING, 
                                win32file.FILE_ATTRIBUTE_NORMAL,
                                None)
        self.config['fd'] = fd
        buf = win32file.ReadFile( fd, 0x200)[1]
        self.config['bss'] = BootSector( buf, 0  ) 
        self.config['mft_dataruns'] = self.__getMFT( 0)
        self.__GenRefArray()

        for fname in self.config['files']:
            self.config['current_file'] = fname[2:] # strip the drive letter off the front
            logging.debug("Copying %s to %s" % (fname, self.config['outputdir']+self.config['current_file']))
            index = 5

            seq_path = [(index,None)]
            tmp_path = fname[3:].split(os.sep)
            table = lookup_table[5]
            for name in fname[3:].split(os.sep):
                name = name.lower()
                if not name in table['children']:
                    break
                table = table['children'][name]
                tmp_path = tmp_path[1:]
                seq_path.append( ( table['seq_num'], name ))
                 
            for name in tmp_path:
                index = table['seq_num']
                logging.debug('Looking for (%s) MFT_INDEX(%016X)' % (name, index))
                ret = self.__getChildIndex( index )
                tmp_index = index
                for en in ret:
                    c_index = en[0] & 0xffffffff
                    c_name = en[1].lower()
                    table['children'][c_name] = { 'name':c_name, 'seq_num':c_index, 'children':{}}
                    if c_name == name.lower():
                        index = c_index
                        seq_path.append( (index, name ) )
                        table = table['children'][c_name]
                        break
                if tmp_index == index:
                    logging.info("%s NOT FOUND" % fname)
                    break
#            if self.config['is_dir'] == True:
            if os.path.isdir(fname ) == True:
                logging.debug(seq_path[-1])
                ret = self.__getChildIndex( seq_path[-1][0] )
                for en in ret: 
                    if en[1].strip() == '' or en[0] == 0:
                        continue
                    logging.debug("\tCopying %s to %s" % (fname+os.sep+en[1], self.config['outputdir']+self.config['current_file']+os.sep+en[1]))
                    self.config['current_file'] = fname[2:]+os.sep+en[1] # strip the drive letter off the front
                    self.__getFile( [en[0]&0xffffffff, en[1]] )
            else:
                self.__getFile( seq_path[-1] )
        if self.config['ignore_table'] == False:
            self.__saveLookuptable( lookup_table )                

