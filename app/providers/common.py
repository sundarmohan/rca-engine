class ProviderError(RuntimeError):
    def __init__(self, category: str, message: str = "Cloud diagnostic unavailable"):
        super().__init__(message)
        self.category = category
