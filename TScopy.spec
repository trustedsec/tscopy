# -*- mode: python ; coding: utf-8 -*-
import platform
import sys

operating_sys = platform.system()

# Computing binary name
suffix = ""
if operating_sys == "Windows":
	if sys.maxsize > 2**32:
		suffix='_x64.exe'
	else:
		suffix= '_x86.exe'

binary_name = "TScopy" + suffix
block_cipher = None


a = Analysis(['tscopy.py'],
             pathex=['Z:\\'],
             binaries=[],
             datas=[],
             hiddenimports=[],
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)
pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name = binary_name,
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=True )
