# Unit tests
## Content
`conftest.py` - a module where setups pytest plugins and contains some base fixtures

`fixtures/` - a directory with pytest fixtures, new module should be also added in `conftest.pytest_plugins`

## How to run tests locally
3. Up docker-compose services
    ```bash
    docker-compose up -d
    ```
4. Run `pytest` within `build_node` container
    ```bash
    docker-compose run --no-deps --rm build_node bash -c 'source env/bin/activate && pytest -vv'
    ```
