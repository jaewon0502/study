# 1534 Count Good Triplets

## Problem Summary
Given an integer array arr and integers a, b, c,
count the number of triplets (i, j, k) such that:

- 0 <= i < j < k < len(arr)
- |arr[i] - arr[j]| <= a
- |arr[j] - arr[k]| <= b
- |arr[i] - arr[k]| <= c

## Common Misunderstandings
- The triplets do NOT have to be consecutive.
- i, j, k represent indices, not values.
- range(a, b) includes a but excludes b.
- Python requires indentation after if statements.

## Approach
1. Use three nested loops to check all valid (i, j, k) combinations.
2. Count the triplet if all conditions are satisfied.

## Time Complexity
- O(n³), which is acceptable for small n.

## Files
- solution.py: Implementation
- tests.py: Unit tests
# 1534 좋은 트리플릿 개수 구하기

## 문제 요약
정수 배열 arr와 a, b, c가 주어질 때,
아래 조건을 만족하는 (i, j, k) 트리플릿의 개수를 구한다.

- 0 <= i < j < k < len(arr)
- |arr[i] - arr[j]| <= a
- |arr[j] - arr[k]| <= b
- |arr[i] - arr[k]| <= c

## 헷갈리기 쉬운 포인트
- 연속된 3개를 고르는 문제가 아니다.
- i, j, k는 값이 아니라 인덱스이다.
- range(a, b)는 b를 포함하지 않는다.
- if 다음 줄에는 반드시 들여쓰기가 필요하다.

## 풀이 접근
1. 3중 반복문으로 모든 (i, j, k) 조합을 검사한다.
2. 모든 조건을 만족하면 카운트를 증가시킨다.

## 시간 복잡도
- O(n³), 입력 크기가 작아 충분히 가능하다.