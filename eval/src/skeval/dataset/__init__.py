"""评测数据集：条目定义、载入与校验。"""

from .schema import Case, DatasetError, load_cases, save_cases, validate_cases

__all__ = ["Case", "DatasetError", "load_cases", "save_cases", "validate_cases"]

