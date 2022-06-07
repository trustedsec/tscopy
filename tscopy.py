#!/usr/bin/env python3

"""
This project is based off the work from the following projects:
* https://github.com/williballenthin/python-ntfs 
* https://github.com/jschicht/RawCopy
"""

# TODO: Parsing of command line input for multiple files needs to be more robust
import logging
import sys
import os
import argparse
import traceback
import time
import ctypes

from TScopy.tscopy import TScopy

log = logging.getLogger("tscopy")
log.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
log.addHandler(handler)

def check_administrative_rights( ):
    if ctypes.windll.shell32.IsUserAnAdmin() == 0:
        log.info("TrustedIR Collector must run with administrative privileges")
        print( "ERROR: TrustedIR Collector must run with administrative privileges\nPress ENTER to finish..." )
        sys.stdin.readline()
        return False
    return True

def parseArgs():
    parser = argparse.ArgumentParser( description="Copy protected files by parsing the MFT. Must be run with Administrator privileges", usage="""\

    TScopy_x64.exe -r -o c:\\test -f c:\\users\\tscopy\\ntuser.dat 
        Description: Copies only the ntuser.dat file to the c:\\test directory 
    TScopy_x64.exe -o c:\\test -f c:\\Windows\\system32\\config 
        Description: Copies all files in the config directory but does not copy the directories under it.  
    TScopy_x64.exe -r -o c:\\test -f c:\\Windows\\system32\\config 
        Description: Copies all files and subdirectories in the config directory.  
    TScopy_x64.exe -r -o c:\\test -f c:\\users\\*\\ntuser*,c:\\Windows\\system32\\config 
        Description: Uses Wildcards and listings to copy any file beginning with ntuser under users accounts and recursively copies the registry hives.
    """)
    parser.add_argument('-f', '--file', help="Full path of the file or directory to be copied. Filenames can be grouped in a comma ',' seperated list. Wildcard '*' is accepted." )   
    parser.add_argument('-o', '--outputdir', help="Directory to copy files too. Copy will keep paths" )   
    parser.add_argument('-i', '--ignore_saved_ref_nums', action='store_true', help="Script stores the Reference numbers and path info to speed up internal run. This option will ignore and not save the stored MFT reference numbers and path")
    parser.add_argument('-r', '--recursive', action='store_true', help="Recursively copies directory. Note this only works with directories.")
    parser.add_argument('--debug', action='store_true', help=argparse.SUPPRESS)
    
    args = parser.parse_args()
    if args.debug:
        log.setLevel(logging.DEBUG)

    if args.file:
        process_files = []
        for name in args.file.split(','):
            process_files.append( name ) 
    else:
        log.error("\nError select --file\n\n")
        parser.print_help()
        sys.exit(1)

    if args.outputdir:
        tmp_dir = args.outputdir
        if tmp_dir[-1] == os.sep:
            tmp_dir = tmp_dir[:-1]

        if not os.path.isdir( tmp_dir ):
            log.error("Error output destination (%s) not found\n\n" %tmp_dir )
            parser.print_help()
            sys.exit(1)
        args.outputdir = tmp_dir
    return { 'files': process_files,
               'outputbasedir': args.outputdir,
               'debug': args.debug,
               'recursive': args.recursive,
               'ignore_table': args.ignore_saved_ref_nums
             }

if __name__ == '__main__':
    start = time.time()    
    args = parseArgs()
    if check_administrative_rights( )  == False:
        sys.exit(1)

    config = {
               'pickledir': args['outputbasedir'],
               'debug': args['debug'],
               'logger': log,
               'ignore_table': args['ignore_table']}
                                                                                
    try:                                                                        
        tscopy = TScopy()
        tscopy.setConfiguration( config )
        dst_path = args['outputbasedir']
        for src in args['files']:
            try:
                tscopy.copy( src, dst_path, bRecursive=args['recursive'])
            except:
                log.error( traceback.format_exc() ) 
    except:
        log.error( traceback.format_exc() ) 

    log.info("Job Took %r seconds" % (time.time()-start))

