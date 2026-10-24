"""개인 웹진: 한 사람의 일주일(또는 하루) 기록으로 웹진 한 호를 만든다. 사용법은 README.md."""
from .issue import list_issues, make_issue

__all__ = ["make_issue", "list_issues"]
