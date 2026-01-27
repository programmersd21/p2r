#pip install black ruff mypy pyupgrade autoflake isort
autoflake --in-place --remove-all-unused-imports --remove-unused-variables --recursive .
pyupgrade --py311-plus $(Get-ChildItem -Recurse -Filter *.py | ForEach-Object { $_.FullName })
isort . --profile black
ruff check . --fix
black .
