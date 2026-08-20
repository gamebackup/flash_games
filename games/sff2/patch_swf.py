import zlib, struct, sys

with open('/home/ashlyn/Documents/GitHub/flash_games/swf_files/sff2_patched_2.swf', 'rb') as f:
    sig = f.read(3)
    ver = struct.unpack('B', f.read(1))[0]
    flen = struct.unpack('<I', f.read(4))[0]
    data = bytearray(zlib.decompress(f.read()))

print(f"Decompressed size: {len(data)}")

def read_u30(data, pos):
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        shift += 7
        pos += 1
        if not (b & 0x80):
            break
    return result, pos

# Strategy: Find all pushfalse (0x2A) followed by setproperty for AUTHORIZED
# in the Main class ABC, and change pushfalse to pushtrue (0x2D)

# The Main ABC tag
main_offset = 1680211
main_len = 11870
tag_data = data[main_offset:main_offset+main_len]

# Find "AUTHORIZED" string in the tag - we know it's at rel_pos 315
# So it's at ABC offset = 315 - (4 + len("com/mcleodgaming/ssf2/Main") + 1)
name = b"com/mcleodgaming/ssf2/Main"
abc_start_in_tag = 4 + len(name) + 1  # flags + name + null
print(f"ABC data starts at offset {abc_start_in_tag} in tag")

# Find AUTHORIZED string in the ABC data
abc_data = tag_data[abc_start_in_tag:]
auth_pos = abc_data.find(b'AUTHORIZED\x00')
if auth_pos < 0:
    print("ERROR: AUTHORIZED not found in ABC data")
    sys.exit(1)

print(f"AUTHORIZED string at ABC offset {auth_pos}")

# Now parse the ABC header to find the string pool and compute multiname index
# We need to count how many strings come before AUTHORIZED

# First, skip the ABC header to find string count
off = 0
minor_ver = struct.unpack_from('<H', abc_data, off)[0]; off += 2
major_ver = struct.unpack_from('<H', abc_data, off)[0]; off += 2
print(f"ABC version {major_ver}.{minor_ver}")

# int_count
int_count, off = read_u30(abc_data, off)
for _ in range(int_count):
    _, off = read_u30(abc_data, off)  # skip int values
print(f"  Skipped {int_count} ints, off={off}")

# uint_count
uint_count, off = read_u30(abc_data, off)
for _ in range(uint_count):
    _, off = read_u30(abc_data, off)
print(f"  Skipped {uint_count} uints, off={off}")

# double_count
double_count, off = read_u30(abc_data, off)
off += double_count * 8
print(f"  Skipped {double_count} doubles, off={off}")

# string_count and strings
string_count, off = read_u30(abc_data, off)
print(f"  String count: {string_count}, off={off}")

auth_string_idx = None
for i in range(string_count):
    s_len, off = read_u30(abc_data, off)
    s_start = off
    off += s_len
    if i < 5 or s_len > 5:
        s = abc_data[s_start:s_start+s_len]
        if i < 10 or s == b'AUTHORIZED':
            try:
                print(f"    [{i}] len={s_len} '{s.decode('latin-1')}'")
            except:
                print(f"    [{i}] len={s_len} {s.hex()}")
    if abc_data[s_start:s_start+s_len] == b'AUTHORIZED':
        auth_string_idx = i
        print(f"  >>> AUTHORIZED is string index {i}")

print(f"\nAfter strings: off={off}")

# namespace_count
ns_count, off = read_u30(abc_data, off)
print(f"  Namespace count: {ns_count}")
for _ in range(ns_count):
    _, off = read_u30(abc_data, off)  # kind
    _, off = read_u30(abc_data, off)  # name

# ns_set_count
ns_set_count, off = read_u30(abc_data, off)
print(f"  NS Set count: {ns_set_count}")
for _ in range(ns_set_count):
    count, off = read_u30(abc_data, off)
    for _ in range(count):
        _, off = read_u30(abc_data, off)

# multiname_count
multiname_count, off = read_u30(abc_data, off)
print(f"  Multiname count: {multiname_count}")

auth_multiname_idx = None
for i in range(multiname_count):
    kind, off = read_u30(abc_data, off)
    if kind in (0x07, 0x0D):  # QName, QNameA
        ns_idx, off = read_u30(abc_data, off)
        name_idx, off = read_u30(abc_data, off)
        if name_idx == auth_string_idx and auth_multiname_idx is None:
            auth_multiname_idx = i
            print(f"  >>> AUTHORIZED multiname index: {i} (QName ns={ns_idx} name={name_idx})")
    elif kind in (0x0F, 0x10):  # RTQName, RTQNameA
        _, off = read_u30(abc_data, off)
    elif kind in (0x11, 0x12):  # RTQNameL, RTQNameLA
        pass
    elif kind in (0x09, 0x0E):  # Multiname, MultinameA
        _, off = read_u30(abc_data, off)
        _, off = read_u30(abc_data, off)
    elif kind in (0x1B, 0x1C):  # MultinameL, MultinameLA
        _, off = read_u30(abc_data, off)
    elif kind in (0x1D, 0x1E):  # TypeName (generic)
        _, off = read_u30(abc_data, off)  # type index
        count, off = read_u30(abc_data, off)  # param count
        for _ in range(count):
            _, off = read_u30(abc_data, off)
    else:
        print(f"  WARNING: Unknown multiname kind {kind} at index {i}, off={off}")
        break

if auth_multiname_idx is None:
    print("ERROR: Could not find AUTHORIZED multiname")
    sys.exit(1)

print(f"\nAUTHORIZED multiname index: {auth_multiname_idx}")

# Now we need to encode this index as u30
def encode_u30(val):
    result = bytearray()
    while True:
        byte = val & 0x7F
        val >>= 7
        if val:
            byte |= 0x80
        result.append(byte)
        if not val:
            break
    return bytes(result)

auth_u30 = encode_u30(auth_multiname_idx)
print(f"AUTHORIZED u30 encoding: {auth_u30.hex()} ({list(auth_u30)})")

# Search for all patterns:
# 1. findproperty (0x5C) + AUTH_IDX + pushfalse (0x2A) + setproperty (0x61) + AUTH_IDX
# 2. findpropstrict (0x5D) + AUTH_IDX + pushfalse (0x2A) + setproperty (0x61) + AUTH_IDX

pattern1 = bytes([0x5C]) + auth_u30 + bytes([0x2A, 0x61]) + auth_u30
pattern2 = bytes([0x5D]) + auth_u30 + bytes([0x2A, 0x61]) + auth_u30

count1 = abc_data.count(pattern1)
count2 = abc_data.count(pattern2)
print(f"\nfindproperty pattern: {count1} matches")
print(f"findpropstrict pattern: {count2} matches")

# Also search more broadly: any pushfalse followed by setproperty AUTHORIZED
# Check if there's a debug instruction between them
search_all = bytes([0x2A, 0x61]) + auth_u30
all_matches = []
idx = 0
while True:
    pos = abc_data.find(search_all, idx)
    if pos < 0:
        break
    all_matches.append(pos)
    idx = pos + 1
print(f"\nAll pushfalse+setproperty(AUTHORIZED) instances: {len(all_matches)}")
for m in all_matches:
    context_before = abc_data[max(0,m-8):m]
    context_after = abc_data[m+len(search_all):m+len(search_all)+8]
    print(f"  offset {m}: ...{context_before.hex()} [2A 61 {auth_u30.hex()}] {context_after.hex()}...")

# Patch: change pushfalse (0x2A) to pushtrue (0x2D) at the right locations
# We want to change ALL pushfalse before setproperty AUTHORIZED to pushtrue
# This ensures AUTHORIZED is always true

tag_data_new = bytearray(tag_data)
abc_data_new = bytearray(abc_data)

patched = 0
for m in all_matches:
    # Verify it's pushfalse at this position
    if abc_data_new[m] == 0x2A:
        abc_data_new[m] = 0x2D
        patched += 1
        print(f"  PATCHED pushfalse -> pushtrue at ABC offset {m}")

# Also need to patch in initResources: the iffalse instruction
# that skips the AUTHORIZED=true code when domain doesn't match
# Looking at the P-code, the iffalse jumps to ofs00c0 (past the AUTHORIZED=true)
# The iffalse opcode is 0x12, followed by s24 (3-byte signed offset)
# We want to change iffalse (0x12) to nop (0x02) but that changes instruction flow
# Better: change the iffalse to always NOT jump, meaning the domain doesn't match case
# will also set AUTHORIZED=true
# 
# But since we already changed the initial value, this isn't strictly needed.
# The AUTHORIZED is initialized to false (0x2A pushfalse), and initResources may
# override it. By changing the initial pushfalse to pushtrue, AUTHORIZED starts true.
# But initResources doesn't set it to false, it only sets it to true in matching cases.
# So if we change the initial value to true, it will ALWAYS be true.
#
# Wait - the initial value is set in the STATIC INITIALIZER of the class, not in initResources.
# Let me verify: the first pattern match should be in the static initializer.
# If there's only one match, that's the one we need.
# If there are multiple, we change them all.

if patched > 0:
    # Update the tag data
    tag_data_new[abc_start_in_tag:abc_start_in_tag + len(abc_data)] = abc_data_new
    
    # Write back to main data
    data[main_offset:main_offset + main_len] = tag_data_new
    
    # Write SWF
    with open(sys.argv[2], 'wb') as f:
        f.write(sig)
        f.write(bytes([ver]))
        compressed = zlib.compress(bytes(data))
        f.write(struct.pack('<I', len(data)))
        f.write(compressed)
    
    print(f"\nSUCCESS! Patched {patched} locations.")
    print(f"Output: {sys.argv[2]}")
    print(f"New file size: {len(sig) + 1 + 4 + len(compressed)} bytes")
else:
    print("\nERROR: No patches applied!")
