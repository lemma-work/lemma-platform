from enum import Enum


class FieldInput(str, Enum):
    CHECKBOX = "checkbox"
    CHOICE = "choice"
    DATE = "date"
    DATETIME = "datetime"
    EMAIL = "email"
    LONG = "long"
    NUMBER = "number"
    PHONE = "phone"
    TEXT = "text"

    def __str__(self) -> str:
        return str(self.value)
