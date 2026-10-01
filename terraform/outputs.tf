output "function_url" {
  description = "Public HTTPS endpoint. Give this to the agent CLI as --backend-url."
  value       = aws_lambda_function_url.api.function_url
}

output "cognito_user_pool_id" {
  description = "Set as cognito_user_pool_id in the backend environment."
  value       = aws_cognito_user_pool.main.id
}

output "cognito_app_client_id" {
  description = "Set as cognito_app_client_id. Auth fails closed without it (Phase 4 / H1)."
  value       = aws_cognito_user_pool_client.backend.id
}

output "github_actions_role_arn" {
  description = "Role the deploy workflow assumes via OIDC. No static keys anywhere."
  value       = module.github_oidc.github_actions_role_arn
}

output "capacity_budget" {
  description = "Provisioned throughput against the 25/25 Always-Free pool."
  # Counted from the tables as deployed, base plus every GSI, not from
  # memory: this line read "14 / 20" for several changes after it stopped
  # being true, and it is the one figure an operator checks before adding
  # a table. A budget that is wrong in the safe direction still teaches
  # the reader to stop believing it.
  value       = "17 RCU / 22 WCU of 25 / 25 - see dynamodb.tf for the per-table split"
}

output "api_live_version" {
  description = "Lambda version the `live` alias serves. Roll back by pointing the alias at an earlier one."
  value       = aws_lambda_alias.api_live.function_version
}

output "alerts_topic_arn" {
  description = "Where every alarm delivers. Checked after each apply: a topic with no CONFIRMED subscription makes every alarm in alarms.tf and probe.tf decorative, and Terraform cannot tell the difference because the subscription resource stays in state either way."
  value       = aws_sns_topic.alerts.arn
}
