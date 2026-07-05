import enum

class DataTier(str, enum.Enum):
    bronze = "bronze"
    silver = "silver"
    gold = "gold"
    unrated = "unrated"
