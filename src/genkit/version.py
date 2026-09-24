"""应用版本的唯一来源：pyproject 通过 setuptools 的 dynamic version 读这里，
`storystudio --version`、设置页、/api/health 也都读这里——改版本号只改这一处。"""

__version__ = "0.1.0"

__all__ = ["__version__"]
