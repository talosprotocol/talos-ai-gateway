from app.domain.a2a.canonical import canonical_json_bytes

def test_canonical_json_sorting():
    obj = {"b": 2, "a": 1, "c": {"z": 26, "y": 25}}
    result = canonical_json_bytes(obj)
    # Expected: {"a":1,"b":2,"c":{"y":25,"z":26}}
    assert result == b'{"a":1,"b":2,"c":{"y":25,"z":26}}'

def test_canonical_json_no_whitespace():
    obj = {"a": 1, "b": [1, 2, 3]}
    result = canonical_json_bytes(obj)
    # No spaces after colons or commas
    assert b" " not in result
    assert result == b'{"a":1,"b":[1,2,3]}'

def test_canonical_json_integer_normalization():
    obj = {"val": 1.0, "nested": [2.0, 3.5]}
    result = canonical_json_bytes(obj)
    # 1.0 -> 1, 2.0 -> 2, but 3.5 stays 3.5
    assert result == b'{"nested":[2,3.5],"val":1}'

def test_canonical_json_unicode():
    obj = {"lang": "tälös"}
    result = canonical_json_bytes(obj)
    # ensure_ascii=False should keep it as raw bytes for UTF-8
    # "tälös" in UTF-8: b't\xc3\xa4l\xc3\xb6s'
    assert b"\\u" not in result
    assert "tälös".encode("utf-8") in result

def test_canonical_json_nested_normalization():
    obj = {
        "data": {
            "score": 100.0,
            "items": [{"id": 1.0}, {"id": 2.5}]
        }
    }
    result = canonical_json_bytes(obj)
    assert result == b'{"data":{"items":[{"id":1},{"id":2.5}],"score":100}}'
