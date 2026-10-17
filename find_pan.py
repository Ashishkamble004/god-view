import hashlib

for i in range(10000):
    pan = f"ABCDE{i:04d}F"
    digest = hashlib.sha256(f"{pan}:".encode()).digest()
    score = 300 + (int.from_bytes(digest[:8], "big") % 600)
    if score >= 680:
        print(f"{pan} -> {score}")
        break
