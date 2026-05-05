"""
Tests for pipeline/scenarios.py — the deterministic parsers. Generation
functions go to the network and are out of scope for the smoke tier.
"""

from pipeline.scenarios import parse_scenarios, parse_multifile_scenarios


def test_parse_scenarios_extracts_three_blocks():
    raw = """SCENARIO: whats_wrong
SETUP:
You are reviewing a Lambda that fans out to SQS.
CODE_OR_CONFIG:
def handler(event, ctx):
    sqs.send_message_batch(Entries=[{"Id": e["id"], "MessageBody": e["body"]} for e in event["Records"]])
QUESTION:
What breaks when Records contains more than 10 items?
ANSWER:
SQS send_message_batch caps at 10 entries per call.
---
SCENARIO: fix_it
SETUP:
Same Lambda, slightly different framing.
CODE_OR_CONFIG:
(omitted)
QUESTION:
How would you fix it?
ANSWER:
Chunk Records into batches of 10.
---
SCENARIO: architecture
SETUP:
The team wants to scale this up 100x.
CODE_OR_CONFIG:
(architecture diagram)
QUESTION:
What changes at 100x volume?
ANSWER:
Move to SNS-fanout or Step Functions for orchestration."""
    out = parse_scenarios(raw)
    assert len(out) == 3
    assert out[0]["scenario"] == "whats_wrong"
    assert "send_message_batch" in out[0]["code_or_config"]
    assert out[2]["scenario"] == "architecture"


def test_parse_scenarios_skips_blocks_missing_required_fields():
    """A block lacking QUESTION+ANSWER is dropped (not raised)."""
    raw = """SCENARIO: whats_wrong
SETUP:
Half a scenario.
---
SCENARIO: whats_wrong
SETUP:
A complete scenario with everything.
CODE_OR_CONFIG:
n/a
QUESTION:
What's wrong?
ANSWER:
Missing error handling."""
    out = parse_scenarios(raw)
    # Only the second block has both QUESTION and ANSWER.
    assert len(out) == 1
    assert "Missing error handling" in out[0]["answer"]


def test_parse_multifile_scenarios_captures_files_and_qa():
    raw = """SCENARIO: multi_file
SETUP:
A Terraform module split across two files.
FILE_1_NAME: main.tf
FILE_1:
resource "aws_s3_bucket" "data" {
  bucket = var.bucket_name
}
FILE_2_NAME: variables.tf
FILE_2:
variable "bucket_name" {}
QUESTION:
Why does terraform plan fail?
ANSWER:
variable "bucket_name" is declared without a default and no .tfvars is provided."""
    out = parse_multifile_scenarios(raw)
    assert len(out) == 1
    s = out[0]
    assert s["scenario"] == "multi_file"
    assert len(s["files"]) == 2
    names = [f["name"] for f in s["files"]]
    assert names == ["main.tf", "variables.tf"]
    assert "aws_s3_bucket" in s["files"][0]["content"]
    assert "Why does terraform plan fail?" in s["question"]
