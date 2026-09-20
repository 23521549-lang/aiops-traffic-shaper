data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd"
  ]
}

resource "aws_iam_role" "github_actions" {
  name = "${var.project_name}-${var.environment}-github-actions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          # Scoped to the deploy environment, NOT "repo:x/y:*". The wildcard
          # let any branch in the repository assume a role that can create IAM
          # roles and Lambdas — a pull request was one push away from the
          # deploy credentials. GitHub only issues this subject after the
          # environment's required reviewers have approved.
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:environment:${var.deploy_environment}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions" {
  name = "github-actions-policy"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ECR is gone: ADR-002 ships a zip through Mangum, not a container, so
      # there is no registry to push to. The old grant was also Resource = "*".

      # Terraform manages the whole stack, so the deploy role must be able to
      # create and update it. Scoped by service and by name where AWS allows.
      {
        Effect = "Allow"
        Action = [
          "lambda:*",
          "cognito-idp:*",
          "events:*",
          "logs:*",
          # Alarms and their notification topic (terraform/alarms.tf). Missed
          # on the first pass: `terraform validate` cannot tell you the deploy
          # role lacks permission for a resource, only that the HCL parses.
          # That gap surfaces at apply time, in front of a human waiting on an
          # approval.
          "cloudwatch:PutMetricAlarm",
          "cloudwatch:DeleteAlarms",
          "cloudwatch:DescribeAlarms",
          "sns:*",
        ]
        Resource = "*"
      },
      {
        # The application's table names are bare (Tenants, Agents, ...) rather
        # than project-prefixed, so they cannot be scoped by prefix. Listing
        # them explicitly is the tightest available scoping — and the fact that
        # it is needed is itself a note for a future rename.
        Effect = "Allow"
        Action = ["dynamodb:*"]
        Resource = [
          for t in ["Tenants", "Agents", "Whitelist", "MitigationState",
          "Models", "TelemetryEvents", "UsageCounters"] :
          "arn:aws:dynamodb:*:${data.aws_caller_identity.current.account_id}:table/${t}"
        ]
      },
      {
        # Creating the Lambda execution roles. The dangerous permission in this
        # policy: iam:PassRole is what lets a caller hand a role to a service.
        # Scoped to roles this project names, so it cannot pass an unrelated
        # privileged role to a function it controls.
        Effect = "Allow"
        Action = [
          "iam:CreateRole",
          "iam:DeleteRole",
          "iam:GetRole",
          "iam:PassRole",
          "iam:TagRole",
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy",
          "iam:PutRolePolicy",
          "iam:DeleteRolePolicy",
          "iam:GetRolePolicy",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies",
        ]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project_name}-*"
      },
      {
        # The Lambda deployment artifact (ADR-004), not the state bucket.
        Effect = "Allow"
        Action = ["s3:*"]
        Resource = [
          "arn:aws:s3:::${var.project_name}-${var.environment}-lambda-artifacts",
          "arn:aws:s3:::${var.project_name}-${var.environment}-lambda-artifacts/*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${var.project_name}-${var.environment}-terraform-state",
          "arn:aws:s3:::${var.project_name}-${var.environment}-terraform-state/*"
        ]
      },
      # The DynamoDB state-lock table is gone: Terraform 1.10+ locks S3 state
      # with a lock file in the same bucket (`use_lockfile` in backend.tf), and
      # that table billed PAY_PER_REQUEST - the one mode ADR-002 names as
      # having no Always-Free allowance. The lock object lives under the state
      # key, so the bucket grant above already covers it. No statement here.

      {
        # The OIDC provider this module creates is part of the same state, so
        # a CI-driven `terraform plan` has to be able to READ it. Without this
        # the pipeline fails on its own trust anchor with AccessDenied - at
        # apply time, in front of whoever just approved the deployment.
        Effect = "Allow"
        Action = [
          "iam:GetOpenIDConnectProvider",
          "iam:CreateOpenIDConnectProvider",
          "iam:DeleteOpenIDConnectProvider",
          "iam:UpdateOpenIDConnectProviderThumbprint",
          "iam:TagOpenIDConnectProvider",
        ]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
      },
      {
        # Reading the AWS-managed AWSLambdaBasicExecutionRole that iam.tf
        # attaches to both functions, and listing providers, are account-wide
        # reads that cannot be resource-scoped.
        Effect = "Allow"
        Action = [
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:ListOpenIDConnectProviders",
        ]
        Resource = "*"
      }
    ]
  })
}

# NOT VERIFIED AGAINST A REAL APPLY. `terraform validate` checks that the HCL
# parses; it cannot tell you a policy is missing an action. The first apply is
# done with the operator's own credentials, so gaps here surface only on the
# first CI-driven deploy - which is the real test of this policy, and has not
# happened. See docs/deployment.md.
