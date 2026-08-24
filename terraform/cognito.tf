# Cognito is what makes `custom:tenant_id` trustworthy. The whole multi-tenant
# isolation story rests on that claim: every dashboard route uses it as the
# DynamoDB partition key and no route accepts a tenant id from the caller. If
# a user could set this attribute themselves, isolation would be theatre —
# hence the attribute is NOT in write_attributes below.

resource "aws_cognito_user_pool" "main" {
  name = "${var.project_name}-users"

  # The claim the backend reads. Immutable: a tenant assignment that a user can
  # change is not an isolation boundary.
  schema {
    name                     = "tenant_id"
    attribute_data_type      = "String"
    mutable                  = false
    developer_only_attribute = false
    required                 = false

    string_attribute_constraints {
      min_length = 1
      max_length = 64
    }
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = false
    require_uppercase                = true
    temporary_password_validity_days = 3
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_client" "backend" {
  name         = "${var.project_name}-backend"
  user_pool_id = aws_cognito_user_pool.main.id

  # No client secret: the token is pasted by a human today and would be handled
  # by a browser flow tomorrow. Neither can keep a secret.
  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  id_token_validity      = 1
  access_token_validity  = 1
  refresh_token_validity = 30
  token_validity_units {
    id_token      = "hours"
    access_token  = "hours"
    refresh_token = "days"
  }

  read_attributes = ["email", "custom:tenant_id"]
  # custom:tenant_id is deliberately absent — a user must not be able to
  # rewrite their own tenant assignment. Only an admin API call sets it.
  write_attributes = ["email"]
}

# admin_auth checks for this group in the cognito:groups claim. Membership is
# the entire Control Platform authorisation model.
resource "aws_cognito_user_group" "admin" {
  name         = "admin"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "Publisher operators — full cross-tenant access via /admin/v1/*."
  precedence   = 1
}
