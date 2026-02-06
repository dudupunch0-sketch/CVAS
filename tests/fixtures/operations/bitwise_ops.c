// Test: All bitwise operations
// Expected: 3 bitwise operations (AND, OR, XOR)
CVAS_START
int bitwise_ops(int a, int b) {
    int and_result = a & b;
    int or_result = a | b;
    int xor_result = a ^ b;
    return and_result + or_result + xor_result;
}
CVAS_END
