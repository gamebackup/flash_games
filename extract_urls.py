import zlib, re

with open(r'D:\GitHub\flash_games\swf_files\sff2.swf', 'rb') as f:
    f.read(8)
    data = f.read()
    decompressed = zlib.decompress(data)
    text = decompressed.decode('latin-1')
    
    # Look for DAT + number patterns (the actual data files)
    dat_file_refs = re.findall(r'DAT\d+\.ssf', text)
    unique_dat = sorted(set(dat_file_refs), key=lambda x: int(re.search(r'\d+', x).group()))
    print("=== DAT*.ssf file references ===")
    for ref in unique_dat:
        print(ref)
    print(f"Total unique DAT files: {len(unique_dat)}")
    
    # Look for data/ directory loading patterns
    print("\n=== data/ directory paths ===")
    data_paths = re.findall(r'data/\w+/', text)
    for p in sorted(set(data_paths)):
        print(p)
    
    # Look for any URL construction patterns
    print("\n=== URL construction near data ===")
    patterns = re.findall(r'.{0,60}data/.{0,60}', text)
    for p in sorted(set(patterns))[:20]:
        print(repr(p))
