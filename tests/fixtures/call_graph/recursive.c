// Test: Recursive function
// Expected: is_recursive: true, has_recursion: true
CVAS_START
int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}
CVAS_END
