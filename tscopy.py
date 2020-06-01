#!/usr/bin/env python

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
import time

from TScopy.tscopy import TScopy

g_logger = logging.getLogger("ntfs.examples.inspect_record")

def parseArgs():
    parser = argparse.ArgumentParser( description="Copy protected files by parsing the MFT. Must be run with Administrator priveledges")
    parser.add_argument('-f', '--file', help="Copies an individual file. Takes third priority if other options are provided" )   
    parser.add_argument('-d', '--directory',help="Directory to copy all contents from. Takes second priority" )   
    parser.add_argument('-l', '--list', help="Comma seperated list of full path files to copy. Takes highest priority." )   
    parser.add_argument('-o', '--outputdir', help="Directory to copy files too. Copy will keep paths" )   
    parser.add_argument('-i', '--ignore_saved_ref_nums', action='store_true', help="Script stores the Reference numbers and path info to speed up internal run. This option will ignore and not save the stored MFT reference numbers and path")
    parser.add_argument('--debug', action='store_true', help=argparse.SUPPRESS)
    
    args = parser.parse_args()
    is_dir = False
    if not args.file and not args.directory and not args.list:
        logging.error("\nError select --file, --directory or --list\n\n")
        parser.print_help()
        sys.exit(1)
#TODO Verify user input is full path if not the complete based off current path
    if args.list:
        process_files = []
        # TODO come back and make this robust and check for " or ' wrapping the long paths
        for name in args.list.split(','):
            process_files.append( name ) 
    elif args.directory:
        is_dir = True
        tmp_dir = args.directory
        if tmp_dir[-1] == os.sep:
            tmp_dir = tmp_dir[:-1]
        if not os.path.isdir( args.directory ):
            logging.error("\nError directory (%s) not found\n\n" % tmp_dir)
            parser.print_help()
            sys.exit(1)
        process_files = [ tmp_dir ]
#        process_files = []
#        for i in os.listdir( tmp_dir):
#            if not os.path.isdir(  tmp_dir + os.sep + i ):
#                process_files.append( tmp_dir + os.sep + i )
        if len( process_files ) == 0:
            logging.error("\nError found no file in directory (%s)\n\n" % tmp_dir)
            sys.exit(1)
    else:
        if not os.path.isfile(args.file):
            logging.error("\nError file (%s) not found\n\n" % args.file)
            parser.print_help()
            sys.exit(1)
        process_files = [ args.file ]

    if args.outputdir:
        tmp_dir = args.outputdir
        if tmp_dir[-1] == os.sep:
            tmp_dir = tmp_dir[:-1]

        if not os.path.isdir( tmp_dir ):
            logging.error("\nError output destination (%s) not found\n\n" %tmp_dir )
            parser.print_help()
            sys.exit(1)
        args.outputdir = tmp_dir
    return { 'files': process_files,
               'is_dir': is_dir,
               'outputdir': args.outputdir,
               'debug': args.debug,
               'ignore_table': args.ignore_saved_ref_nums
             }

if __name__ == '__main__':
    start = time.time()    
    config = parseArgs()
    rc = TScopy()
    rc.setConfig( config )
    rc.copy( )
    logging.info("Job Took %r seconds" % (time.time()-start))

