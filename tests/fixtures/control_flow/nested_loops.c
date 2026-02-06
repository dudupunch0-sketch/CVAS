// Test: Nested loops
// Expected: 2 loops in CFG, max_nesting_depth: 2
CVAS_START
int nested_sum(int n) {
    int sum = 0;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            sum += i + j;
        }
    }
    return sum;
}
CVAS_END
