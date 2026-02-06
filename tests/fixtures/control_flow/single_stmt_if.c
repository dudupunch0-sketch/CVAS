// Test: if without braces (single statement)
// Expected: Should NOT have "without braces" limitation
CVAS_START
int clamp_positive(int x) {
    if (x < 0) return 0;
    return x;
}
CVAS_END
