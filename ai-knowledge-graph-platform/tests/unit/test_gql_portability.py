from graphrag.graph.gql_portability import ReadQueryContract, validate_read_contracts


def test_default_contracts_are_bounded_parameterised_reads() -> None:
    assert validate_read_contracts() == []


def test_validation_rejects_writes_and_unbounded_queries() -> None:
    invalid = ReadQueryContract(
        name="unsafe",
        parameters=("tenant",),
        cypher="MATCH (e:Entity {tenant: $tenant}) CREATE (x:Entity) RETURN e",
        gql="MATCH (e:Entity {tenant: $tenant}) RETURN e",
    )

    errors = validate_read_contracts((invalid,))

    assert "unsafe/cypher contains forbidden CREATE" in errors
    assert "unsafe/cypher must bound results" in errors
    assert "unsafe/gql must bound results" in errors
