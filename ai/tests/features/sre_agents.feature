Feature: SRE agent structured planning

  As the operator of the SRE workspace
  I want the planner agent to always return schema-valid JSON
  so that downstream graph nodes never choke on free text.

  Scenario: planner emits a valid plan
    Given the local deterministic provider is configured
    When I ask the planner to plan an incident
    Then I receive a plan object
    And the plan has an owner and a list of steps

  Scenario: log analyzer returns a root cause
    Given the local deterministic provider is configured
    When I ask the log analyzer to diagnose an alert
    Then I receive a root cause object
    And the root cause has a confidence score between 0 and 1
