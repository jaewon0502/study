def max_subarray_sum_with_one_deletion(nums):
    dp0 = nums[0]          # no deletion
    dp1 = float('-inf')    # one deletion used
    answer = nums[0]

    for i in range(1, len(nums)):
        dp1 = max(dp1 + nums[i], dp0)
        dp0 = max(dp0 + nums[i], nums[i])
        answer = max(answer, dp0, dp1)

    return answer

tests = [
    [1, -2, 0, 3],
    [-1, -2, -3],
    [2, 1, -5, 4],
    [5],
    [1, -1, 1, -1, 1]
]

for t in tests:
    print(t, "->", max_subarray_sum_with_one_deletion(t))