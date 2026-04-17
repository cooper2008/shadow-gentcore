---
id: rds-multi-az-failover
triggers: [rds_instance_unhealthy, rds_failover_required]
estimated_duration: 15m
blast_radius: single_db
approval_required: true
industry: aws-ops
---

# RDS Multi-AZ Failover Runbook

Authoritative procedure for failing over an RDS instance to its Multi-AZ
standby. Used by `_shared/ExecuteAgent/v1` via the `core/runbook_retrieval`
toolpack; retrieved at execute-stage time for the acme-aws-ops domain.

## When to Run

- CloudWatch `DatabaseConnections` saturation on primary >90% for 5 min
- RDS event `DB_INSTANCE_UNHEALTHY`
- Manual ops action following a regional network degradation

## Preconditions

1. Instance is Multi-AZ enabled. Verify:
   ```
   aws rds describe-db-instances --db-instance-identifier $DB_ID \
     --query 'DBInstances[0].MultiAZ'
   ```
   Must return `true`. If `false`, this runbook does NOT apply.

2. Standby replica is healthy. Verify:
   ```
   aws rds describe-db-instances --db-instance-identifier $DB_ID \
     --query 'DBInstances[0].StatusInfos[?StatusType==`read replication`]'
   ```

## Procedure

### Step 1: Announce the failover
- Post to `#inc-${INCIDENT_ID}` Slack channel: "Starting RDS failover for
  `$DB_ID` per runbook rds-multi-az-failover."
- Tag on-call DBA for audit.

### Step 2: Snapshot before failover (safety)
```
aws rds create-db-snapshot \
  --db-instance-identifier "$DB_ID" \
  --db-snapshot-identifier "pre-failover-$(date +%s)"
```

Wait for snapshot state `available` (~2–5 min for small DBs):
```
aws rds wait db-snapshot-completed \
  --db-snapshot-identifier "pre-failover-$(date +%s)"
```

### Step 3: Initiate failover
```
aws rds reboot-db-instance \
  --db-instance-identifier "$DB_ID" \
  --force-failover
```

### Step 4: Verify failover completed
Poll instance status until `available`:
```
aws rds wait db-instance-available --db-instance-identifier "$DB_ID"
```

Check the AZ changed:
```
aws rds describe-db-instances \
  --db-instance-identifier "$DB_ID" \
  --query 'DBInstances[0].AvailabilityZone'
```

### Step 5: Validate connectivity
- Run the application health check endpoint (`$APP_HEALTH_URL`).
- Check recent error rate on application CloudWatch dashboard.
- Expected: error rate returns to baseline within 2 minutes.

### Step 6: Close out
- Post to `#inc-${INCIDENT_ID}`: "RDS failover complete. Primary now
  in AZ `$NEW_AZ`. Connectivity confirmed. Incident can be closed
  pending post-mortem."
- Open a Jira ticket in `INC` project with label `post-mortem-required`.

## Rollback

If the failover itself fails (rare — usually network-level), RDS will
automatically attempt to fail back. If manual rollback is needed, use
the snapshot from Step 2 to restore to a new instance and update the
application connection string.

## Out of Scope

- Single-AZ instances (this runbook does not apply — use
  `rds_single_az_recovery.md` if it exists, otherwise escalate).
- Cross-region failover (different runbook: `rds_cross_region_dr.md`).
- Application-layer failover (handled by ALB / Route 53 policies).
