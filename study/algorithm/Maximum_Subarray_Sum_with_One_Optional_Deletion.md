Problem Description

Given an integer array nums, choose a non-empty contiguous subarray.

You are allowed to delete at most one element from the chosen subarray.
Deleting an element is optional.

Return the maximum possible sum of the resulting subarray.

	•	1 ≤ len(nums) ≤ 100,000
	•	-10,000 ≤ nums[i] ≤ 10,000

Input: [1, -2, 0, 3]
Output: 4
Explanation: Delete -2 → subarray [1, 0, 3]


Input: [-1, -2, -3]
Output: -1
Explanation: Choose the largest single element without deletion

Approach

This problem is an extension of the classic maximum subarray (Kadane’s algorithm).

To handle the deletion constraint, we track two states:
	•	dp0
Maximum subarray sum ending at the current index without deletion
	•	dp1
Maximum subarray sum ending at the current index with one deletion already used

State Transitions
	•	dp0 = max(dp0 + nums[i], nums[i])
	•	dp1 = max(dp1 + nums[i], previous dp0)

The answer is the maximum value among all dp0 and dp1.


def max_subarray_sum_with_one_deletion(nums):
    dp0 = nums[0]          # no deletion
    dp1 = float('-inf')    # one deletion used
    answer = nums[0]

    for i in range(1, len(nums)):
        dp1 = max(dp1 + nums[i], dp0)
        dp0 = max(dp0 + nums[i], nums[i])
        answer = max(answer, dp0, dp1)

    return answer

What i learned

Key Takeaways
Simplifying Logic through State Separation By separating states, even complex conditions can be expressed as simple recurrence relations.

State Dependency and Order The current state (e.g., dp1) must be built upon the previous state (e.g., dp0) to maintain logical consistency.

Handling All-Negative Cases Even cases consisting entirely of negative numbers are handled naturally without requiring separate edge-case logic.