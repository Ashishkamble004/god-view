"""Synthetic checksum-invalid Indian PAN and Aadhaar number generators and validators.

Enforces BR-01 and TDD §6.2 #9:
1. Synthetic PAN (`[A-Z]{5}[0-9]{4}[A-Z]{1}`):
   - Always uses an invalid 4th-character holder status class (never in P, C, H, F, A, T, B, L, J, G).
   - Always uses a 10th character that intentionally mismatches the mod-26 check character of the first 9 characters.
2. Synthetic Aadhaar (`^[2-9][0-9]{11}$`):
   - Always uses a 12th digit that intentionally fails the Verhoeff dihedral group D5 checksum (`verhoeff_checksum != 0`).
"""

from __future__ import annotations

import hashlib
import re

PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$")
AADHAAR_REGEX = re.compile(r"^[2-9][0-9]{11}$")

# Official Income Tax PAN 4th-character status classes (real documents use these)
VALID_PAN_FOURTH_CHARS: frozenset[str] = frozenset(
    {"P", "C", "H", "F", "A", "T", "B", "L", "J", "G"}
)

# Synthetic-only 4th-character classes (disjoint from VALID_PAN_FOURTH_CHARS)
INVALID_PAN_FOURTH_CHARS: tuple[str, ...] = ("Z", "X", "Q", "W", "U", "V", "Y")

# Verhoeff dihedral group D5 multiplication table
_VERHOEFF_D: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

# Verhoeff permutation table
_VERHOEFF_P: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

# Verhoeff inverse table
_VERHOEFF_INV: tuple[int, ...] = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_checksum(number_str: str) -> int:
    """Compute the Verhoeff checksum of a numeric string (0 means Verhoeff-valid)."""
    if not number_str or not number_str.isdigit():
        raise ValueError(f"Verhoeff input must be digits only, got '{number_str}'")
    c = 0
    for i, ch in enumerate(reversed(number_str)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c


def verhoeff_expected_check_digit(prefix_11: str) -> int:
    """Compute the Verhoeff check digit that would make a 12-digit number valid."""
    if len(prefix_11) != 11 or not prefix_11.isdigit():
        raise ValueError(f"Expected 11 digits, got '{prefix_11}'")
    c = 0
    for i, ch in enumerate(reversed(prefix_11)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return _VERHOEFF_INV[c]


def is_verhoeff_valid(aadhaar_number: str) -> bool:
    """Return True iff `aadhaar_number` is a 12-digit string passing Verhoeff checksum."""
    if not AADHAAR_REGEX.match(aadhaar_number):
        return False
    return verhoeff_checksum(aadhaar_number) == 0


def generate_synthetic_aadhaar(seed: str | int) -> str:
    """Generate a 12-digit synthetic Aadhaar number guaranteed to fail Verhoeff validation."""
    digest = hashlib.sha256(f"aadhaar:{seed}".encode()).digest()
    first_digit = 2 + (digest[0] % 8)  # 2..9
    middle_digits = "".join(str(digest[i] % 10) for i in range(1, 11))
    prefix_11 = f"{first_digit}{middle_digits}"
    valid_check = verhoeff_expected_check_digit(prefix_11)
    # Intentionally offset the check digit by 1..9 so verhoeff_checksum != 0
    invalid_check = (valid_check + 1 + (digest[11] % 9)) % 10
    candidate = f"{prefix_11}{invalid_check}"
    assert verhoeff_checksum(candidate) != 0
    return candidate


def compute_pan_check_char(first_nine: str) -> str:
    """Compute a deterministic mod-26 check character over the first 9 PAN characters."""
    if len(first_nine) != 9:
        raise ValueError(f"Expected 9-character PAN prefix, got '{first_nine}'")
    total = sum((idx + 1) * ord(ch) for idx, ch in enumerate(first_nine))
    return chr(ord("A") + (total % 26))


def is_valid_real_pan(pan_number: str) -> bool:
    """Return True iff `pan_number` has a valid real 4th-char class and matching check char."""
    if not PAN_REGEX.match(pan_number):
        return False
    if pan_number[3] not in VALID_PAN_FOURTH_CHARS:
        return False
    return pan_number[9] == compute_pan_check_char(pan_number[:9])


def is_checksum_invalid_pan(pan_number: str) -> bool:
    """Return True iff `pan_number` matches 10-char PAN regex but has an invalid 4th-char class and invalid check char."""
    if not PAN_REGEX.match(pan_number):
        return False
    return pan_number[3] not in VALID_PAN_FOURTH_CHARS and pan_number[9] != compute_pan_check_char(
        pan_number[:9]
    )


def generate_synthetic_pan(seed: str | int) -> str:
    """Generate a 10-char synthetic PAN with an invalid 4th-char class and invalid check char."""
    digest = hashlib.sha256(f"pan:{seed}".encode()).digest()
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    c0 = letters[digest[0] % 26]
    c1 = letters[digest[1] % 26]
    c2 = letters[digest[2] % 26]
    # 4th character (index 3) strictly from INVALID_PAN_FOURTH_CHARS
    c3 = INVALID_PAN_FOURTH_CHARS[digest[3] % len(INVALID_PAN_FOURTH_CHARS)]
    c4 = letters[digest[4] % 26]
    digits = f"{((digest[5] << 8) | digest[6]) % 10000:04d}"
    if digits == "0000":
        digits = "1042"
    first_nine = f"{c0}{c1}{c2}{c3}{c4}{digits}"
    valid_check = compute_pan_check_char(first_nine)
    # Pick a 10th letter guaranteed != valid_check
    offset = 1 + (digest[7] % 25)
    invalid_check = chr(ord("A") + ((ord(valid_check) - ord("A") + offset) % 26))
    pan = f"{first_nine}{invalid_check}"
    assert is_checksum_invalid_pan(pan)
    return pan
