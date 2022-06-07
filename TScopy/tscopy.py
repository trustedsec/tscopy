"""
This project is based off the work from the following projects:
* https://github.com/williballenthin/python-ntfs 
* https://github.com/jschicht/RawCopy
"""
# TODO: Will have issues with non ascii characters in files names
# TODO: Currently only processes '\\.\' where RawCopy supported other formats
import sys
import os
import re
import pickle
import traceback

from math import ceil
from TScopy.BinaryParser import hex_dump, Block
from TScopy.MFT import INDXException, MFTRecord, ATTR_TYPE, Attribute_List
from TScopy.MFT import INDEX_ROOT

os_sep = str.encode(os.sep)

if os.name == "nt":
    try:
        import win32file, win32api, win32con
    except:
        print( "Must have pywin32 installed -- pip install pywin32" )
        sys.exit(1)

####################################################################################
# BootSector structure
#   https://flatcap.org/linux-ntfs/ntfs/files/boot.html
####################################################################################
class BootSector(Block):
    def __init__(self, buf, offset, logger):
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
        

####################################################################################
#  NTFS INDX Record structure
#     https://flatcap.org/linux-ntfs/ntfs/concepts/index_record.html 
####################################################################################
class INDX( Block ):
    def __init__(self, buf, offset ):
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

####################################################################################
#  NTFS INDX Entry Structure
#     https://flatcap.org/linux-ntfs/ntfs/concepts/index_entry.html
####################################################################################
class INDX_ENTRY( Block ):
    def __init__(self, buf, offset):
        super(INDX_ENTRY, self).__init__(buf, offset)
        self.declare_field("qword", "mft_recordnum", 0)
        self.declare_field("word", "entry_sz", 0x08 )
        if self.entry_sz() == 0x18 and self.mft_recordnum() == 0:
            raise INDXException("End of INDX File found")
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


####################################################################################
#  The main class of TScopy.
#     * Is a singleton instance
#     * Example usage
#       config = {'outputbasedir':dst, 'pickledir':dir,'logger':log,'debug':False,'ignore_table':False}
#       tscopy = TScopy()
#       tscopy.setConfiguration( config )
#       tscopy.copy( src, dst )
#
#     * Config key descriptions
#       - outputbasedir : The FULL PATH of directory where the files will be copied too.
#       - pickledir : The FULL PATH of directory where the pickle file will be created or used.
#       - logger : A preconfigured instance of the python Logger class. 
#       - debug : Not used
#       - ignore_table: 
#           * True  = Rebuilds the MFT table from the root node and does not save the table at the end of the run
#           * False = Uses a previous mft.pickle file if found. Saves the file after every copy.
####################################################################################
class TScopy( object ):
    _instance = None
    def __new__( cls ):
        if cls._instance == None:
            cls._instance = super(TScopy, cls).__new__(cls)
            cls.__isConfigured = False
            cls.__pickle_filename = b"mft.pickle"
            cls.config = { 'files': None,
                            'pickledir': None,
                            'logger': None,
                            'debug': True,
                            'ignore_table':False,
                          }
            cls.__useWin32 = False
        return cls._instance

    ####################################################################################
    #  isConfigured:  Verifies that the object has  been configured at least once
    ####################################################################################
    def isConfigured( self ):
        return self.__isConfigured

    ####################################################################################
    # setConfiguration:  Parses the config dictionary to set the values for debug, logger,
    #                    lookuptable and the picke directory
    ####################################################################################
    def setConfiguration( self, config ):
        if self.__isConfigured == True:
            return
        self.__MFT_lookup_table = None
        self.__isConfigured = True
        self.setDebug( config['debug'] )
        self.setLogger( config['logger'] )
        self.setLookupTable( config['ignore_table'] )
        self.setPickleDir( config['pickledir'] )


    ####################################################################################
    # SetLogger:  Sets the class object logger variable
    #       Needs to be preconfigured
    ####################################################################################
    def setLogger( self, logger ):
        if logger == None:
            raise Exception( "TSCOPY", "Invalid Logger")
        self.config['logger'] = logger

    ####################################################################################
    # setDebug: Sets the class object debugger variable
    ####################################################################################
    def setDebug( self, debug ):
        self.config['debug'] = debug

    ####################################################################################
    # setLookuptable: Sets the class object ignore_table.
    ####################################################################################
    def setLookupTable( self, tf ):
        self.config['ignore_table'] = tf

    ####################################################################################
    #  setPickleDir: Sets the output directory to save the mft.pickle file too
    ####################################################################################
    def setPickleDir( self, directory ):
        if not directory == None and not os.path.isdir( directory ):
            self.config['logger'].error("Error pickle destination (%s) not found" % directory)
            raise Exception( "TSCOPY", "Error pickle destination (%s) not found" % directory)
        self.__pickle_fullpath = b'%s%s%s' % ( str.encode(directory), os_sep, self.__pickle_filename )
        self.__MFT_lookup_table = self.__getLookupTableFromDisk( "c" )
        
    ####################################################################################
    #  __getLookupTableFromDisk: Checks the mft.pickle file. 
    #       If it exists then it loads into memory.
    #       If it does not exists then it creates a new basic structure
    ####################################################################################
    def __getLookupTableFromDisk( self, drive_letter ):
        if not os.path.isfile( self.__pickle_fullpath):
            return {drive_letter:{5:{'seq_num': 5, 'name':'','children':{}}}}
        try:
            self.config['logger'].debug("Using Pickle file: %s " % self.__pickle_fullpath)
            with open( self.__pickle_fullpath, 'rb') as fd:
                return pickle.loads( fd.read() )
        except:
            raise Exception( "TSCOPY", "FAILED to parse pickle file %s" % self.__pickle_fullpath )
        
    ####################################################################################
    #  __saveLookuptable: Write the lookup table from memory to disk. 
    #       Overwrites previous copy if it exists.
    ####################################################################################
    def __saveLookuptable( self, lookup_table ):
        with open(self.__pickle_fullpath, 'wb') as fd:
            fd.write( pickle.dumps( lookup_table ))

    ####################################################################################
    # __getMFT: Gets the root record of the MFT 
    ####################################################################################
    def __getMFT( self, index=0 ):
        fd = self.config['fd']
        bss = self.config['bss']
        mft_offset = bss.bytes_per_sector() * bss.sectors_per_cluster() * bss.start_c_mft()
        if self.__useWin32 == False:
            mft_offset = 0x400
#        win32file.SetFilePointer( fd, mft_offset+(index*bss.mft_record_size ), win32file.FILE_BEGIN)
#        buf = win32file.ReadFile( fd, bss.mft_record_size )[1]
        buf, buf_sz = self.__read( fd, mft_offset+(index*bss.mft_record_size ), bss.mft_record_size )
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

    ####################################################################################
    #  __GenRefArray: Iterates through the seq_num 5 datadruns 
    ####################################################################################
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

    ####################################################################################
    #  __process_image: TODO 
    ####################################################################################
    def __process_image( self, targetDrive ):
        pass

    ####################################################################################
    # __search_mft: Iterates through the target files path, populating the table and seq_path
    #           with each branch of the path as it parses the MFT records. The search ends when 
    #           it fails to find the next item in the target path or the target is identified.
    #       table: The pointer to the current location into the mft metadata table stored in memory
    #       tmp_path: The target directory path as a list
    #       seq_path: A list of the found target dirctory path with mft sequesnce numbers   
    ####################################################################################
    def __search_mft( self, table, tmp_path, seq_path ):
        for name in tmp_path:
            index = table['seq_num']
#            self.config['logger'].debug('Looking for (%s) MFT_INDEX(%016X)' % (name, index))
            ret = self.__getChildIndex( index )
#            self.config['logger'].debug("childindex = %r" % len(ret) )
            tmp_index = index
            for seq_num in ret:
                c_index = seq_num & 0xffffffff
                c_name = ret[seq_num].lower()
                if type(c_name ) is str:
                    c_name = str.encode(c_name)
                self.config['logger'].debug("search_mft:: c_name %r " % c_name)
                table['children'][c_name] = { 'name':c_name, 'seq_num':c_index, 'children':{}}
                if c_name == name.lower():
                    index = c_index
                    seq_path.append( (index, c_name ) )
                    table = table['children'][c_name]
                    break
            if tmp_index == index:
#                self.config['logger'].info("%s NOT FOUND" % name)
                return None, None, None
        return table, tmp_path, seq_path
    ####################################################################################
    #  __find_last_known_path: Iterates through the target files path and matches with the 
    #           currently known indexes in the table. Returns as soon as the next path item 
    #           is not found or the end target has been located.
    #       table: The pointer to the current location into the mft metadata table stored in memory
    #       tmp_path: The target directory path as a list
    #       seq_path: A list of the found target dirctory path with mft sequesnce numbers   
    ####################################################################################

    def __find_last_known_path( self, table, tmp_path, seq_path  ):
        l_path = tmp_path[:]
        for name in l_path:
            name = name.lower()
            if not name in table['children']:
                break
            table = table['children'][name]
            tmp_path = tmp_path[1:]
            seq_path.append( ( table['seq_num'], name ))
        return table, tmp_path, seq_path

    ####################################################################################
    #  __copydir: Copies the entire directory. If bRecursive this function calls itself with 
    #           any child drictories
    #       fname: fullpath of the dirctory to copy
    #       index: Sequence number of the MFT record of the parent:
    #       table: Pointer to the current index in the MFT metadata table
    #       bRecursive:  
    #           True: When the parents child is a directory __copydir is called recursivly
    #           False: Does not copy child directories
    ####################################################################################
    def __copydir( self, fname, index, table, bRecursive=False):
        self.config['logger'].debug('fname(%r) index(%r)' % (fname, index) )
        table = self.__copydirfiles( fname, index, table )

        if bRecursive == True:
            for dirs in table['children']:
                l_table = table['children'][dirs]
                c_index = l_table['seq_num']
                buf, buf_sz = self.__calcOffset( c_index )
                if buf == None or buf_sz == 0:
                    raise Exception("Failed to process mft_offset")
                record = MFTRecord(buf, 0, None)
                if record.is_directory():
                    self.config['logger'].debug( "Next Directory %r  %r %r" % (c_index, dirs, fname))
                    self.config['current_file'] = fname[2:]
                    self.__copydir( os.path.join(fname,dirs), c_index, l_table, bRecursive=True )
        
    ####################################################################################
    # __copydirfiles: Wraps __getFile and copies all the files under the current directory
    #       fname: fullpath of the dirctory to copy
    #       index: Sequence number of the MFT record of the parent:
    #       table: Pointer to the current index in the MFT metadata table
    ####################################################################################
    def __copydirfiles( self, fname, index, table ):
        self.config['logger'].debug( "copydirfiles \n\tfname:\t%r\n\tindex:\t%r\n\ttable %r" % (fname,index,table))
        if table['children'] == {}:
            ret = self.__getChildIndex( index )
            self.config['logger'].debug( "\tchildren: %r" % len(ret))
            for seq_num in ret: 
                c_index = seq_num & 0xffffffff
                c_name = ret[seq_num].lower()
                if type(c_name) is str:
                    c_name = str.encode(c_name)
                self.config['logger'].debug( "copydirfiles :: c_name %r" % (c_name))
                table['children'][c_name] = { 'name':c_name, 'seq_num':c_index, 'children':{}}

                if ret[seq_num].strip() == '' or seq_num == 0:
                    continue

        tmp_filename = self.config['current_file']
        for name in table['children']:
            seq_num = table['children'][name]['seq_num']
            self.config['logger'].debug("\tCopying %s to %s" % (name, tmp_filename))
            self.config['logger'].debug("\tCopying %s to %s" % (fname+os_sep+name, self.config['outputbasedir']+tmp_filename+os_sep+name))

            self.config['current_file'] = fname[2:]+os_sep+name # strip the drive letter off the front
            if b'*' in fname[2:]+os_sep+name:
                self.config['current_file'] = tmp_filename+os_sep+name # strip the drive letter off the front
                
            self.__getFile( [seq_num&0xffffffff, name] )
        return table

    ####################################################################################
    #  __copyfile: Internal copy function. Used to setup and parse target filename, locate
    #           previously identified paths in the mft metadata list. and then copy the file/
    #           files/ or direcotories
    #       filename: Full path to the target file/directory or wildcarded to copy
    #       mft_filename: TODO remove
    #       bRecursive: 
    #           True:  Copy all children from this directory on
    #           False: Do not copy children
    ####################################################################################
    def __copyfile( self, filename, mft_filename=None, bRecursive=False ):
        if self.__useWin32 == True:
            self.config['logger'].debug( 'filename %r' % filename)
            if not filename[:4].lower() == b'\\\\.\\':
                targetDrive = b'\\\\.\\'+filename[:2]
            else:
                targetDrive = filename[:6]
            
            driveLetter = targetDrive[-2]
            self.config['logger'].debug( 'Target Drive %s' % targetDrive)
            self.config['logger'].debug( 'DriveLetter %s' % driveLetter)

            self.__process_image( targetDrive ) # TODO process this to determin correct offsets

            if self.config['ignore_table'] == True:
                self.__MFT_lookup_table = {driveLetter:{5:{'seq_num':5,'name':'','children':{}}}}
            elif not driveLetter in self.__MFT_lookup_table.keys():
                self.__MFT_lookup_table = self.__MFT_lookup_table[driveLetter] = {5:{'seq_num':5,'name':'','children':{}}}
#            self.config['logger'].debug( 'Target Drive %s' % driveLetter)
        else:
            self.__MFT_lookup_table = {"c":{5:{'seq_num':5,'name':b'','children':{}}}}
            targetDrive = mft_filename
            driveLetter = b"c"
            self.config['logger'].debug( 'Processing the %s MFT file' % targetDrive )

        self.config['driveLetter'] = driveLetter
        fd = self.__open( targetDrive )
        self.config['fd'] = fd
        buf, buf_sz = self.__read( fd, 0, 0x200 ) #        buf = win32file.ReadFile( fd, 0x200)[1]
        self.config['bss'] = BootSector( buf, 0, self.config['logger'] ) 
        self.config['mft_dataruns'] = self.__getMFT( 0)
        self.__GenRefArray()

        fname = filename 
        index = 5
        
        try:
            # Find the last known directory in the MFT_lookup_table
            seq_path = [(index,None)]
            tmp_path = fname[3:].split(os_sep)
            table = self.__MFT_lookup_table[driveLetter][5]

            expandedWildCards = self.__process_wildcards( filename, table )
            if expandedWildCards == False:
                cp_files = [ tmp_path ]
            else:
                cp_files = expandedWildCards

            
            for cp_file in cp_files:
                self.config['current_file'] = os_sep.join(cp_file) # strip the drive letter off the front
                l_fname = fname[:3] + self.config['current_file']
                self.config['logger'].info(b"Copying %s to %s" % (l_fname, self.config['outputbasedir']+self.config['current_file']))
                table, tmp_path, seq_path = self.__get_file_mft_seqid( cp_file )
                
                # Index was not located exit (error message already logged)
                if table == None:
                    self.config['logger'].error("File Not Found"  )
                    return

                # Check the mft structure if this is a directory
                index = seq_path[-1][0]
                buf, buf_sz = self.__calcOffset( index )
                if buf == None or buf_sz == 0:
                    raise Exception("Failed to process mft_offset")
                record = MFTRecord(buf, 0, None)
                if record.is_directory():
                    self.__copydir( l_fname, index, table, bRecursive=bRecursive )
                else:
                    self.__getFile( seq_path[-1] )
        except:
            self.config['logger'].error(traceback.format_exc())
        finally:
            if self.config['ignore_table'] == False:
                self.__saveLookuptable( self.__MFT_lookup_table)                

    ####################################################################################
    # __isSplitMFT: Determines if the MFT record is split
    ####################################################################################
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

    ####################################################################################
    #  __GetChildIndex: Parses the MFT records to find all children of the current sequence ID
    #       index: Sequence ID or seq_num of the current MFT record to extract and parse
    ####################################################################################
    def __getChildIndex( self, index  ):
        fd = self.config['fd']
        bss = self.config['bss']
        bpc = bss.bytes_per_cluster

        buf, buf_sz = self.__calcOffset( index )
        if buf == None or buf_sz == 0:
            raise Exception("Failed to process mft_offset")
        record = MFTRecord(buf, 0, None)
        if not record.is_directory():
            return []
        ret  = {}
        for attribute in record.attributes():
            if attribute.type() == ATTR_TYPE.INDEX_ROOT:
                for entry in INDEX_ROOT(attribute.value(), 0).index().entries():
                    refNum = entry.header().mft_reference() & 0xfffffffff
                    if refNum in ret:
                        if b"~" in ret[refNum]:
                            self.config['logger'].debug( "GetChildIndex:001: entry.filename %r" % entry.filename_information().filename() )
                            ret[refNum] = entry.filename_information().filename()  
                    else:
                        self.config['logger'].debug( "GetChildIndex:002: entry.filename %r" % entry.filename_information().filename() )
                        ret[refNum] = str.encode(entry.filename_information().filename())  
            elif attribute.type() == ATTR_TYPE.ATTRIBUTE_LIST:
                self.config['logger'].debug("ATTRIBUTE_LIST HAS BEEN FOUND 0x(%08x)!!!!" % index )
                attr_list = Attribute_List(attribute.value(), 0, attribute.value_length(), self.config['logger'] )
                self.config['logger'].debug(hex_dump(attribute.value()[:attribute.value_length()]))
                a_list = []
                for entry in attr_list.get():
                    if (entry.type() == ATTR_TYPE.INDEX_ROOT or entry.type() == ATTR_TYPE.INDEX_ALLOCATION ) and not (entry.baseFileReference()&0xffffffff) == index:
                        if not entry.baseFileReference() in a_list:
                            a_list.append( entry.baseFileReference() & 0xffffffff   )
                for next_index in a_list:
                    # WARNING!!! Recursive
                    if index == next_index:
                        self.config['logger'].debug(hex_dump(attribute.value()[:attribute.value_length()]))
#                        raise Exception("Attribute_list failed to parse.")
                        continue
                    rec_children = self. __getChildIndex( next_index )
                    self.config['logger'].debug("ATTRIBUTE_LIST index(%d) children (%r) " % (next_index, rec_children) )
                    ret.update( rec_children )
            elif attribute.type() == ATTR_TYPE.INDEX_ALLOCATION:
                for cluster_offset, length  in attribute.runlist().runs():
                    offset=cluster_offset*bpc
                    buf, buf_sz = self.__read( fd, offset, length*bpc)
                    for cnt in range(length):
                        idx_buf = buf[cnt*bpc:(cnt+2)*bpc]
                        ind = INDX( idx_buf, 0 )
                        idx_buf = ind.update_seq_arr( idx_buf )
                        entry_offset = ind.index_entries_offset()+0x18 
                        i = 0 
                        last_i = i
                        while i < ind.index_entries_sz() :
                            try:
                                entry  = INDX_ENTRY( idx_buf, entry_offset )
                                refNum = entry.mft_recordnum() & 0xfffffffff
                                if refNum in ret:
                                    self.config['logger'].debug( "GetChildIndex:: ret[refNum] %r" %(ret[refNum]))
                                    if b"~" in ret[refNum]:
                                        ret[refNum] = entry.filename().replace(b'\x00',b'')
                                else:
                                    self.config['logger'].debug( "GetChildIndex:003: entry.filename %r" % entry.filename().replace(b'\x00',b''))
                                    ret[refNum] = entry.filename().replace(b'\x00',b'')
                            except   INDXException:
                                break
                            except:
                                self.config['logger'].error(traceback.format_exc())
                                self.config['logger'].debug( 'len(idx_buf (%03x) entry_offset(%03x)' % ( len(idx_buf), entry_offset))
                                pass
                            entry_offset += entry.entry_sz()

                            i += entry.entry_sz()
                            if entry.entry_sz() == 0:
                                break
        return ret

    ####################################################################################
    # __calcOffset: Calculates the offset into the drive to locat the specific data 
    #       for the taget sequence Number
    #   target_seq_num: Sequence ID to copy form the disk
    ####################################################################################
    def __calcOffset( self, target_seq_num ):
        fd = self.config['fd']
        bss = self.config['bss']
        mft_vcn = self.config['mft_dataruns']
        image_offset = 0 # TODO: Change this when finished processing the image
        array = self.config['split_mft_rec']

        # Handle in the case that the object is split accross two dataruns
        split = self.__isSplitMFT( array, target_seq_num )
        if not split == None:
#            self.config['logger'].debug( 'calcOffset: a split record was detected' )
            item = array[split]
            ind = item.index('?')
            testRef = item[0:ind]   
            if not int(testRef) == target_seq_num:
#                self.config['logger'].debug("Error: The ref in the array did not match target ref.")
                return None
            
            srecord3 = item[ind+1:]
            srecordArr = srecord3.split('|')
            if not len( srecordArr ) == 3:
#                self.config['logger'].debug("Error: Array contained more elements than expected: %d" % len( srecordArr ))
                return None

            record = ""
            record_sz = 0
            for i in srecordArr:
                if not ',' in i: 
#                    self.config['logger'].debug('Split:: Could not find ","')
                    continue
                ind = i.index(',')
                srOffset = i[:ind]
                srSize   = i[ind+1:]
#                win32file.SetFilePointer( fd, srOffset + image_offset, win32file.FILE_BEGIN)
#                record += win32file.ReadFile( fd, srSize)[1]
                buf, buf_sz = self.__read( fd, srOffset + image_offset, srSize )
                record  += buf
                record_sz += buf_sz
            return record, record_sz
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
#            win32file.SetFilePointer( fd, mft_offset, win32file.FILE_BEGIN)
#            return win32file.ReadFile( fd, bss.mft_record_size )[1]
            if self.__useWin32 == False:
                mft_offset = 0x400 + 0x400*target_seq_num
#            self.config['logger'].debug('Split:: mft_offset(%r) record_size(%r)' % ( mft_offset, bss.mft_record_size))
            return self.__read( fd, mft_offset, bss.mft_record_size )
        return None

    ####################################################################################
    #  __parse_attribute_data: Processes the files data sections and combines them to 
    #            create the file.
    #       attribute: The data attribute from the MFT record 
    #       Returns the dat content 
    ####################################################################################
    def __parse_attribute_data( self, attribute, output_name ):
        ret = b''
        fd = self.config['fd']
        out_name = output_name
        bpc = self.config['bss'].bytes_per_cluster
        filename = attribute.name()
#        import pdb; pdf.set_trace()
        try:
            self.config['logger'].debug("Attribute File Name %s" % attribute.name())
            if attribute.name_length() > 0:
                out_name += b"_ADS_%s" % attribute.name()
            fd_out = open(out_name, "wb")
            self.config['logger'].debug("non_resident %r" % attribute.non_resident() )
            if attribute.non_resident() == 0:
                fd_out.write( attribute.value() )
            else:
                cnt = 0
                padd = False
                for cluster_offset, length in attribute.runlist().runs():
                    read_sz = length * bpc 

                    if cluster_offset == 0: ## Sparsed file segment detected
                        self.config['logger'].debug("parse_attribute_data:: Sparsed file segment detected  length( %08x ) lengthx4096 (%08x)" % ( length, read_sz))
                        chunk_sz = 0x1000
                        chunk = b"\x00"*chunk_sz
                        while cnt < read_sz:
                            if read_sz-cnt > chunk_sz:
                                chunk_sz = read_sz-cnt
                            fd_out.write(chunk[:chunk_sz])
                            cnt += chunk_sz
                    else:
                        self.config['logger'].debug("GetFile:: cluster_offset( %08x ) length( %08x )  " % ( cluster_offset, length))
                        self.config['logger'].debug("readsize %08x cnt %08x init_sz %08x" % ( read_sz, cnt, attribute.initialized_size()))
                        if read_sz + cnt > attribute.initialized_size():
                            read_sz = attribute.initialized_size() - cnt
                            padd = True
                        if (read_sz % 0x1000) > 0:
                            read_sz += 0x1000 - (read_sz%0x1000)
                        offset=cluster_offset * bpc
    
                        self.config['logger'].debug("readsize %08x cnt %08x init_sz %08x" % ( read_sz, cnt, attribute.initialized_size()))
                        name = ''

                        # Detected ADS
                        buf, buf_sz = self.__read( fd, offset, read_sz, fd_out )
    
                        if attribute.data_size() < cnt + read_sz:
                            read_sz = attribute.data_size()-cnt
                        cnt += read_sz
                            
                        if padd == True:
                            padd_sz  = attribute.data_size() - attribute.initialized_size()
                            ret += b'\x00' * padd_sz
                            cnt += padd_sz
                        if cnt > attribute.initialized_size():
    #                        self.config['logger'].debug("readsize %08x cnt %08x init_sz %08x" % ( read_sz, cnt, attribute.initialized_size()))
                            break
        except:
            self.config['logger'].error('Failed to get file %s\n%s' % (filename, traceback.format_exc() ))

    ####################################################################################
    # __parse_file_record: Given the sequence ID parse the contents of the file from the 
    #           MFT and return as a string.
    #       mft_file_seq_id: The sequence ID of the MFT record to return the data from
    ####################################################################################
    def __parse_file_record( self, mft_file_seq_id, output_name ):
        self.config['logger'].debug("parse_fle_record 0x%08x" % mft_file_seq_id)
        buf, buf_sz = self.__calcOffset( mft_file_seq_id )
        if buf == None:
            raise Exception("Failed to process mft_offset")

        record = MFTRecord(buf, 0, None)
        if record.is_directory():
            return None

        ret_val = {}
        for attribute in record.attributes():
            self.config['logger'].debug("Parsing Attribute 0x%2x" % attribute.type() )
            if attribute.type() == ATTR_TYPE.ATTRIBUTE_LIST:
                file_contents = b''
                self.config['logger'].debug("ATTRIBUTE_LIST HAS BEEN FOUND getting the File 0x(%08x)!!!!" % mft_file_seq_id)
                attr_list = Attribute_List(attribute.value(), 0, attribute.value_length(), self.config['logger'] )
                a_list = []
                for entry in attr_list.get():
                    if entry.type() == ATTR_TYPE.DATA and not (entry.baseFileReference()&0xffffffff) == mft_file_seq_id:
                        if not entry.baseFileReference() in a_list:
                            a_list.append( entry.baseFileReference() & 0xffffffff   )
                for next_index in a_list:
                    if mft_file_seq_id == next_index:
                        continue
                    # WARNING RECURSION
                    self.__parse_file_record( next_index, output_name )
            elif attribute.type() == ATTR_TYPE.DATA:
                self.__parse_attribute_data( attribute, output_name )

    ####################################################################################
    # __getFile: The required file was identified this function locates all the parts of 
    #           the file and writes them in order to the destination location
    #       mft_file_object:
    ####################################################################################
    def __getFile( self, mft_file_object ):
        file_contents = b''
        try:
            fullpath = self.config['outputbasedir'] + self.config['current_file']
            #        self.config['logger'].debug( "GetFile:: fullpath %s" % fullpath )
            #        self.config['logger'].debug( "GetFile:: attributes %s" % attribute.get_all_string())
            path = os_sep.join(fullpath.split(os_sep)[:-1])
            winapi_path = self.__winapi_path(path)
            if not os.path.isdir(winapi_path):
                os.makedirs(winapi_path)
            self.config['logger'].debug("GetFile:: fullpath edit %s" % fullpath)
            self.__parse_file_record( mft_file_object[0], self.__winapi_path(fullpath) )
        except:
            self.config['logger'].error('Failed to get file %s\n%s' % (mft_file_object[1], traceback.format_exc() ))

    ####################################################################################
    # __winapi_path: Convert Filepath to Unicode to bypass win32 filepath length limit of 260
    ####################################################################################
    def __winapi_path( self, filename, encoding='utf-8'):
        if (not isinstance(filename, str) and encoding is not None):
            filename = filename.decode(encoding)
        path = os.path.abspath(filename)
        if path.startswith(u"\\\\"):
            return u"\\\\?\\UNC\\" + path[2:]
        return u"\\\\?\\" + path


    ####################################################################################
    # __open: Wrapper around win32file createfile. 
    ####################################################################################
    def __open( self, filename ):
        fd = None
        try:
            if self.__useWin32 == False:
                fd = open(filename, 'rb') 
            else:
                t_filename = filename.decode("utf-8")
                fd = win32file.CreateFile( t_filename,
                                win32file.GENERIC_READ,
                                win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
                                None, 
                                win32con.OPEN_EXISTING, 
                                win32file.FILE_ATTRIBUTE_NORMAL,
                                None)
        except:
            self.config['logger'].error( traceback.format_exc())
        return fd

    ####################################################################################
    # __read: Wrapper around win32file set file pointer and read contents.
    #   fd => the handle to the file to be copied
    #   offset => number of bytes to skip of the file
    #   read_sz => Number of bytes to read from the file
    #   fd_output => Default None. If none then read into buffer otherwise
    #                The handle to the output file
    ####################################################################################
    def __read( self, fd, offset, read_sz, fd_output=None ):
        bytes_read = 0
        buf = b""
        if type(offset) == float:
            offset = int(offset)
        try:
            if self.__useWin32 == False:
                fd.seek( offset, 0)
                if read_sz > 0x10000000:
                    read_step = 0x01500000
                    buf = b''
                    while bytes_read <= read_sz:
                        if not fd_output == None:
                            fd_output.write(fd.read( read_step ))
                            bytes_read += read_step
                        else:
                            buf += fd.read( read_step )
                            bytes_read += read_step
                else:
                    if not fd_output == None:
                        fd_output.write(fd.read(read_sz))
                        bytes_read += read_sz
                    else:
                        buf += fd.read(read_sz)
                        bytes_read += read_sz
            else:
                if read_sz > 0x10000000:
                    read_step = 0x01500000
                    buf = b''
                    while bytes_read <= read_sz:
                        win32file.SetFilePointer( fd, offset + bytes_read, win32file.FILE_BEGIN)
                        if not fd_output == None:
                            fd_output.write( win32file.ReadFile( fd, read_step)[1] )
                            bytes_read += read_step
                        else:
                            buf += win32file.ReadFile(fd, read_step)[1]
                            bytes_read += read_step
                else:
                    win32file.SetFilePointer( fd, offset, win32file.FILE_BEGIN)
                    if not fd_output == None:
                        buff =  win32file.ReadFile( fd, read_sz)[1]
                        fd_output.write( buff )
                        bytes_read = read_sz
                    else:
                        buf += win32file.ReadFile( fd, read_sz)[1]
                        bytes_read = read_sz
        except:
            self.config['logger'].error( traceback.format_exc())
            self.config['logger'].debug("offset(%08x), readsize (%08x) fd (%08x)" % ( offset, read_sz, fd))
            self.config['logger'].debug("stack %s" % traceback.print_stack() )
        return (buf, bytes_read)

    ####################################################################################
    # __get_wildcard_children:  Get the children of the wildcarded directory location
    #       path: is a tuple containing the base path and the wildcard
    # TODO Move this someplace else in the file
    ####################################################################################
    def __get_wildcard_children( self, path ):
        copy_list = []
        table, x, seq_path = self.__get_file_mft_seqid( path[0] )
        if seq_path == None:
            return copy_list
        # Test if the last value seq_path[-1] is the directory we are looking for
        if path[1] == None:
            if seq_path[-1][1] == path[0][-1]:
                copy_list.append( path[0] )

        # get children of found path and find all that match wildcard.
        ret = self.__getChildIndex( seq_path[-1][0] )
        for x in ret:
            if path[1] == None:
                    break
            l_name = ret[x].lower()
            l_reg = re.escape(path[1]).replace(b'\\*', b'.*')
            if not l_reg[-1] == b'*':
                l_reg += b'$'
            if re.match( l_reg, l_name ):
                l_name =  path[0] + [ l_name ] 
                copy_list.append( l_name )
        return copy_list

    ####################################################################################
    # __get_file_mft_seqid: Wrapper used to search for the file in the current memory mft 
    #           metadata list then process the rest of the path from parsing the MFT
    #       tmp_path: List of the source path
    ####################################################################################
    def __get_file_mft_seqid( self, tmp_path ):
        index = 5
        seq_path = [(index,None)]
        table = self.__MFT_lookup_table[self.config['driveLetter']][index]
        table, tmp_path, seq_path = self.__find_last_known_path( table, tmp_path, seq_path  )
        table, tmp_path, seq_path = self.__search_mft( table, tmp_path, seq_path )
        return table, tmp_path, seq_path

    ####################################################################################
    # __process_wildcards: Called when a wildcard was detected in the source filename.
    #           Parses the wildcards and breaks up into sections then the paths are expanded
    #           and each matching record is copied.
    #       filename: Filename containing the wildcards
    #       table: Pointer to the root of the mft Metadata table
    ####################################################################################
    def  __process_wildcards( self, filename, table ):
        filename = filename.lower()
        if not b'*' in filename:
            return False
        if filename[1:3] == ":\\":
            filename = filename[3:]
        
        index = 5
        seq_path = [(index,None)]
        tmp_path = filename.split( os_sep )
        path = []
        path_start = 0
        for ind in range( len(tmp_path)):
            if "*" in tmp_path[ind]:
                path.append( ( tmp_path[ path_start : ind ], tmp_path[ind]) )
                path_start = ind + 1
        if path_start < len(tmp_path):
            path.append( ( tmp_path[ path_start : ], None) )

        tList = []
        for iPath in path:
            tList = self.__regexsearch( iPath, tList ) 
        return tList

    ####################################################################################
    # __regexsearch: Searches the path to determine if it matches the wildcard. Only the
    #           '*' wildcard is supported. 
    #       path:
    #       tList:
    ####################################################################################
    def __regexsearch( self, path, tList ):
        if tList == []:
            findPaths = [ path ]
        else:
            findPaths = []
            for ePath in tList:
                findPaths.append( ( ePath + path[0], path[1] ))
        ret = []
        for fp in findPaths:
            found =  self.__get_wildcard_children( fp )
            ret.extend( found )
        return ret

            
    def __get_local_drives(self):
        """Returns a list containing letters from local drives"""
        drive_list = win32api.GetLogicalDriveStrings()
        drive_list = drive_list.split("\x00")[0:-1]  # the last element is ""
        list_local_drives = []
        for letter in drive_list:
            if win32file.GetDriveType(letter) == win32file.DRIVE_FIXED:
                list_local_drives.append(letter)
        return list_local_drives
    
    ####################################################################################
    # Copy file from a single source file or directory. Wildcards (*) are acceptable
    #   src_filename: Can be a filename, directory, or a wildcard
    #   dest_filename: The root directory to save files too. Each will create a mirror path
    #                  Example: dest_filename = 'c:\test\' and copying "c:\windows\somefile" 
    #                           the output file will have the path of "c:\test\windows\somefile"
    #   bRecursive: Tells the copy to recursivly copy a directory. Only works with directories
    ####################################################################################
    def copy( self, src_filename, dest_filename, bRecursive=False ):
        self.__useWin32 = True
        if type(dest_filename) == str:
            dest_filename = str.encode(dest_filename)
        if not (dest_filename[-1] == b'/' or dest_filename[-1] == b'\\'):
            dest_filename = dest_filename+os_sep
        self.config['outputbasedir'] = dest_filename 
        if type(src_filename) == str:
            src_filename = str.encode(src_filename)
        if not type( src_filename ) == bytes:
            self.config['logger'].error("INVALID src type (%r)" % (src_filename ) )
            return
        src_filename = os.path.abspath( src_filename )
        src_filename = [ src_filename ]
        for filename in src_filename: 
            driveLetter = None
            if self.__useWin32 == True:
                self.config['logger'].debug( 'filename %r %r' % (type(filename),filename))
                if not filename[:4].lower() == b'\\\\.\\':
                    targetDrive = b'\\\\.\\'+filename[:2]
                else:
                    targetDrive = filename[:6]
                
                driveLetter = targetDrive[-2]
            if driveLetter == '*':
                for drive in self.__get_local_drives():  
                    self.__copyfile( filename.replace(b"*", drive[0], 1), bRecursive=bRecursive )
            else:
                self.__copyfile( filename, bRecursive=bRecursive )






