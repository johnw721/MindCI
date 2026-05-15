"""
Tests for pipeline.scenarios.guess_extension — language/format detection
used by the dashboard's code download buttons.
"""

from pipeline.scenarios import guess_extension


def test_guesses_terraform_from_resource_block():
    code = 'resource "aws_s3_bucket" "data" {\n  bucket = "x"\n}'
    assert guess_extension(code) == ("tf", "text/plain")


def test_guesses_yaml_from_apiversion_kind():
    code = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: foo"
    assert guess_extension(code) == ("yaml", "text/yaml")


def test_guesses_python_from_def_or_import():
    assert guess_extension("def handler(event, ctx):\n    return {}") == ("py", "text/x-python")
    assert guess_extension("from boto3 import client\n\nctx = ...") == ("py", "text/x-python")


def test_guesses_json_from_object_or_array_start():
    assert guess_extension('{"foo": 1}') == ("json", "application/json")
    assert guess_extension('[1, 2, 3]')  == ("json", "application/json")


def test_falls_back_to_txt_for_unknown_content():
    assert guess_extension("just some prose, not code") == ("txt", "text/plain")
    assert guess_extension("") == ("txt", "text/plain")
