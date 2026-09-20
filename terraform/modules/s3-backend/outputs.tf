output "bucket_name" {
  value = aws_s3_bucket.terraform_state.bucket
}

output "dynamodb_table_name" {
  description = "Empty unless create_lock_table is true; S3-native locking replaced it."
  value       = try(aws_dynamodb_table.terraform_lock[0].name, "")
}
