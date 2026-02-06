// Test: else-if chain
// Expected: Multiple conditional_branch blocks
CVAS_START
int classify(int value) {
    if (value < 0) {
        return -1;
    } else if (value == 0) {
        return 0;
    } else if (value < 10) {
        return 1;
    } else {
        return 2;
    }
}
CVAS_END
