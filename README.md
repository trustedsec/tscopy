# TScopy
![TScopy Logo](/README_imgs/Blog_061120.png)

Updated 2022-06-06

## Introducing TScopy 
It is a requirement during an Incident Response (IR) engagement to have the ability to analyze files on the filesystem. Sometimes these files are locked by the operating system (OS) because they are in use, which is particularly frustrating with event logs and registry hives. TScopy allows the user, who is running with administrator privileges, to access locked files by parsing out their raw location in the filesystem and copying them without asking the OS.

There are other tools that perform similar functions, such as RawCopy, which we have used and is the basis for this tool. However, there are some disadvantages to RawCopy that led us to develop TScopy, including performance, size, and the ability to incorporate it in other tools.

This blog is intended to introduce TScopy but also to ask for assistance. As in all software development, the more a tool is used, the more edge cases can be found. We are asking that people try out the tool and report any bugs.

## What is TScopy?
TScopy is a Python script used to parse the NTFS $MFT file to locate and copy specific files. By parsing the Master File Table (MFT), the script bypasses operating system locks on files. The script was originally based on the work of RawCopy. RawCopy is written in AutoIT and is difficult to modify for our purposes. The decision to port RawCopy to Python was done because of the need to incorporate this functionality natively into our toolset.

TScopy is designed to be run as a standalone program or included as a python module. The python implementation makes use of the python-ntfs tools found at https://github.com/williballenthin/python-ntfs. TScopy built upon the base functionality of python-ntfs to isolate the location of each file from the raw disk.

## What makes TScopy different?
TScopy is written in Python and organized into classes to make it more maintainable and readable than AutoIT. AutoIT can be flagged as malicious by anti-virus or detections software because some malware has utilized its potential.

The major difference between TScopy and RawCopy is the ability to copy multiple files per execution and to cache the file structure. As shown in the image below, TScopy has options to download a single file, multiple comma delimited files, the contents of a directory, wildcarded paths (individual files or directories), and recursive directories. 

TScopy caches the location of each directory and file as it iterates the target file’s full path. It then uses this cache to optimize the search for any other files, ensuring future file copies are performed much faster. This is a significant advantage over RawCopy, which iterates over the entire path for each file.

## TScopy Options
```
.\TScopy_x64.exe -h

usage: 
    TScopy_x64.exe -r -o c:\test -f c:\users\tscopy\ntuser.dat 
        Description: Copies only the ntuser.dat file to the c:\test directory 
    TScopy_x64.exe -o c:\test -f c:\Windows\system32\config 
        Description: Copies all files in the config directory but does not copy the directories under it.  
    TScopy_x64.exe -r -o c:\test -f c:\Windows\system32\config 
        Description: Copies all files and subdirectories in the config directory.  
    TScopy_x64.exe -r -o c:\test -f c:\users\*\ntuser*,c:\Windows\system32\config 
        Description: Uses Wildcards and listings to copy any file beginning with ntuser under users accounts and recursively copies the registry hives.
    

Copy protected files by parsing the MFT. Must be run with Administrator privileges

optional arguments:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  Full path of the file or directory to be copied.
                        Filenames can be grouped in a comma ',' seperated
                        list. Wildcard '*' is accepted.
  -o OUTPUTDIR, --outputdir OUTPUTDIR
                        Directory to copy files too. Copy will keep paths
  -i, --ignore_saved_ref_nums
                        Script stores the Reference numbers and path info to
                        speed up internal run. This option will ignore and not
                        save the stored MFT reference numbers and path
  -r, --recursive       Recursively copies directory. Note this only works with
                        directories.
```
There is a hidden option ‘--debug’, which enables the debug output.

## Examples
```code
TScopy_x64.exe -f c:\windows\system32\config\SYSTEM -o e:\outputdir
```
Copies the SYSTEM registry to e:\outputdir
The new file will be located at e:\outputdir\windows\system32\config\SYSTEM
```code
TScopy_x64.exe -f c:\windows\system32\config\SYSTEM -o e:\outputdir -i
```
Copies the SYSTEM registry to e:\outputdir but ignores any previous cached files and does not save the current cache to disk

```code
TScopy_x64.exe -f c:\windows\system32\config\SYSTEM,c:\windows\system32\config\SOFTWARE -o e:\outputdir
```
Copies the SYSTEM and the SOFTWARE registries to e:\outputdir

```code
TScopy_x64.exe -f c:\windows\system32\config\ -o e:\outputdir
```
Copies the contents of the directory config to e:\outputdir

```code
TScopy_x64.exe -r -f c:\windows\system32\config\ -o e:\outputdir
```
Recursively copies the contents of the directory config to e:\outputdir

```code
TScopy_x64.exe  -f c:\users\*\ntuser.dat -o e:\outputdir
```
Copies each users NTUSER.DAT file to e:\outputdir

```code
TScopy_x64.exe  -f c:\users\*\ntuser.dat* -o e:\outputdir
```
For each users copies all files that begin with NTUSER.DAT to e:\outputdi

```code
TScopy_x64.exe  -f c:\users\*\AppData\Roaming\Microsoft\Windows\Recent,c:\windows\system32\config,c:\users\*\AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt -o e:\outputdir
```
For each users copies all jumplists, Registry hives, and Powershell history commands to e:\outputdi

## Bug Reporting Information
Please report bugs in the issues section of the GitHub page.

## Bug Fixes and Enhancements 
### Version 4.0
- Corrected copying file containing sparsed data. Issue #13 (Error copying c:\$extend\$usnjrnl$j)
- Files are no longer read into memory before writing to disk. Writes are performed by data run read now. Should reduce memory usage on large files.
### Version 3.0
- Added Support for Alternative Data Stream. Request the root file and the ADS streams are copied
- WildCard for the drive letter. Fixed Drives only.  Example "\*:\$MFT"  will find the $MFT for all local drives
- Logging issues. Failed copies are reporting failed again.
- Filepath size limit of 256 removed.
### Version 2.0
- Issue 1: Change sys.exit to raise Exception
- Issue 2: The double copying of files. Full name and short name.
- Issue 3: Added the ability to recursively copy a directory
- Issue 4: Add the support for wildcards in the path. Currently only supports *
- Issue 5: Removed the hardcoded MFT size. MFT size determined by the Boot Sector
- Issue 6: Converted the TScopy class into a singleton. This allows the class to be instantiated once and reuse the current MFT metadata object for all copies.
- Issue 7: Attribute type ATTRIBUTE_LIST is now being handled.
- Issue 9: Attrubute type ATTRIBUTE_LIST was not handled for files. THis caused a silent failure for files like SOFTWARE regestry hive.
- Changes: General comments have been added to the code
- Changes: Input parameters have changed. Reduced the three(3) different options --file, --list, and --directory to --file.
- Changes: Backend restructuring to support new features.

## TODO:
