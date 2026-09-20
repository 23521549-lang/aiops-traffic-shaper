data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# One role per function rather than a shared one: the retrain Lambda has no
# business reading the Agents table or writing MitigationState, and the API
# Lambda has no business writing to Models. Least privilege here costs two
# resources and buys a real blast-radius reduction if either is compromised.

resource "aws_iam_role" "api" {
  name               = "${var.project_name}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role" "retrain" {
  name               = "${var.project_name}-retrain"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "api_logs" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "retrain_logs" {
  role       = aws_iam_role.retrain.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- API Lambda: everything the request path touches ----------------------
data "aws_iam_policy_document" "api_data" {
  statement {
    # No DeleteItem here. The request path removes exactly one thing - a
    # whitelist entry - and that is granted separately below. A tenant or an
    # agent record is never deleted by serving traffic, so the API role should
    # not be able to; cheapest possible protection for data that cannot be
    # reconstructed.
    actions = [
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]
    resources = concat(
      [
        aws_dynamodb_table.tenants.arn,
        aws_dynamodb_table.agents.arn,
        aws_dynamodb_table.whitelist.arn,
        aws_dynamodb_table.mitigation_state.arn,
        aws_dynamodb_table.telemetry_events.arn,
        aws_dynamodb_table.usage_counters.arn,
      ],
      [
        "${aws_dynamodb_table.agents.arn}/index/*",
        "${aws_dynamodb_table.telemetry_events.arn}/index/*",
      ],
    )
  }

  # The one legitimate delete in the whole request path: a tenant removing an
  # IP from their own whitelist.
  statement {
    actions   = ["dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.whitelist.arn, aws_dynamodb_table.mitigation_state.arn]
  }

  # Models is read-only from the request path. Only the retrain function
  # promotes a model; the API serving traffic must never be able to.
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.models.arn]
  }
}

resource "aws_iam_role_policy" "api_data" {
  name   = "${var.project_name}-api-data"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api_data.json
}

# --- Retrain Lambda: read telemetry, read/write models --------------------
data "aws_iam_policy_document" "retrain_data" {
  statement {
    actions = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:Scan"]
    resources = [
      aws_dynamodb_table.telemetry_events.arn,
      "${aws_dynamodb_table.telemetry_events.arn}/index/*",
      aws_dynamodb_table.tenants.arn,
      aws_dynamodb_table.whitelist.arn,
    ]
  }
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.models.arn]
  }
}

resource "aws_iam_role_policy" "retrain_data" {
  name   = "${var.project_name}-retrain-data"
  role   = aws_iam_role.retrain.id
  policy = data.aws_iam_policy_document.retrain_data.json
}
