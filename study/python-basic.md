# Python Basics Notes

## abs
- abs(x) returns the absolute value of x.
- abs(a - b) measures the distance between two numbers.

## range
- range(n): 0 to n-1
- range(a, b): a to b-1
- b is excluded.

## Indentation
- Python uses indentation to define code blocks.
- Code under if, for, while must be indented.

# Python Basics & Lessons Learned

## 1. Basic behavior of if statement
- An if statement executes its block only when the condition is True.
- If the condition is False and there is no else, Python simply skips the block.

## 2. Conditional branching syntax (if / elif / else)
- Python does not support `else if`.
- Use `elif` instead.

Example:
if condition1:
    ...
elif condition2:
    ...
else:
    ...

## 3. Difference between pass and continue
- pass does nothing and execution continues normally.
- continue immediately ends the current loop iteration and moves to the next one.

## 4. While loops and index range
- If n = len(nums), valid indices are from 0 to n - 1.
- Using <= n will cause an IndexError.

Safe condition:
while index < n:

## 5. Nested while loop structure
- The outer loop selects the current reference element.
- The inner loop compares it with all other elements.

## 6. Skipping self-comparison
- Many problems require i != j.
- The cleanest solution is to check first and use continue.

## 7. Important rule when using continue
- Loop variables must still be updated before continue.
- Otherwise, an infinite loop may occur.

## 8. Python indentation rules
- Python uses indentation to define code blocks.
- Unexpected indentation causes an IndentationError.

## 9. Meaningful variable naming
- Variable names should clearly describe their purpose.
- Good naming improves readability and reduces mistakes.

## 10. Key takeaways from this problem
- Most bugs come from loop boundaries and control flow.
- Clear structure is more important than clever tricks.

## One-line summary
- Write loops that are safe, readable, and explicit.

# 파이썬 기초 정리

## abs 함수
- abs(x)는 절댓값을 반환한다.
- abs(a - b)는 두 수의 차이 크기이다.

## range 함수
- range(n): 0부터 n-1까지
- range(a, b): a부터 b-1까지
- b는 포함되지 않는다.

## 들여쓰기
- 파이썬은 들여쓰기로 코드 블록을 구분한다.
- if, for, while 아래 코드는 반드시 들여써야 한다.

# 파이썬 기본 문법과 배운 점 정리

## 1. if 문 기본 동작
- if 문은 조건이 True일 때만 내부 코드가 실행된다.
- 조건이 False이고 else가 없으면 아무 일도 일어나지 않고 넘어간다.

## 2. 조건 분기 문법 (if / elif / else)
- 파이썬에는 else if 문법이 없다.
- 반드시 elif를 사용해야 한다.

예시:
if 조건1:
    ...
elif 조건2:
    ...
else:
    ...

## 3. pass 와 continue 차이
- pass는 아무 동작도 하지 않고 아래 코드를 계속 실행한다.
- continue는 현재 반복을 즉시 종료하고 다음 반복으로 넘어간다.

## 4. while 문과 인덱스 범위
- n = len(nums)일 때 유효한 인덱스는 0부터 n - 1까지다.
- <= n을 사용하면 IndexError가 발생한다.

안전한 조건:
while index < n:

## 5. 중첩 while 루프 구조
- 바깥 루프는 기준이 되는 원소를 선택한다.
- 안쪽 루프는 모든 원소와 비교한다.

## 6. 자기 자신 비교 제외
- 많은 문제에서 i != j 조건이 필요하다.
- 가장 깔끔한 방법은 먼저 검사하고 continue로 건너뛰는 것이다.

## 7. continue 사용 시 주의점
- continue를 사용할 때도 인덱스 증가는 반드시 필요하다.
- 그렇지 않으면 무한 루프에 빠질 수 있다.

## 8. 파이썬 들여쓰기 규칙
- 파이썬은 들여쓰기로 코드 블록을 구분한다.
- 잘못된 들여쓰기는 IndentationError를 발생시킨다.

## 9. 의미 있는 변수 이름
- 변수 이름은 역할이 명확해야 한다.
- 좋은 이름은 가독성을 높이고 실수를 줄인다.

## 10. 이번 문제의 핵심 교훈
- 대부분의 버그는 루프 범위와 제어 흐름에서 발생한다.
- 꼼수보다 명확한 구조가 훨씬 중요하다.

## 한 줄 요약
- 안전하고 읽기 쉬우며 명확한 루프를 작성하자.