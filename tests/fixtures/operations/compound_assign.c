// Test: Compound assignment operators
// Expected: Operations for +=, *=, etc.
CVAS_START
int compound_assign(int x) {
    x += 5;   // Should normalize to x = x + 5
    x *= 2;   // Should normalize to x = x * 2
    x++;      // Should normalize to x = x + 1
    return x;
}
CVAS_END
