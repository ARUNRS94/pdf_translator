from dataclasses import dataclass


@dataclass
class TextSpan:
    page_number: int
    block_no: int
    line_no: int
    span_no: int
    text: str
    bbox: tuple[float, float, float, float]
    font: str
    size: float
    color: int
    flags: int


@dataclass
class TranslationChunk:
    span_indexes: list[int]
    payload: str
