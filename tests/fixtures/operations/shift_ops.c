// Test: Shift operations
// Expected: 2 shift operations (left and right)
CVAS_START
int shift_ops(int value) {
    int left = value << 2;
    int right = value >> 1;
    return left + right;
}
CVAS_END
