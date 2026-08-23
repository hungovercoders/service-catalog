Feature: Placing an order
  As a customer
  I want to place an order
  So that it is fulfilled once payment settles

  These scenarios are acceptance criteria, not suggestions. An
  implementation that fails any of them is wrong. If a scenario is
  itself wrong, that is a versioned change to this file.

  Background:
    Given the customer "c-1001" exists

  Scenario: A valid order is accepted and an event is enqueued
    When the customer places an order for 2 units of "SKU-RED" at 1250 pence
    Then the response status is 201
    And the order status is "placed"
    And the order total is 2500 pence
    And an "OrderPlaced" event is written to the outbox in the same transaction

  Scenario: The outbox and the order commit together
    Given the event broker is unavailable
    When the customer places an order for 1 unit of "SKU-RED" at 1250 pence
    Then the response status is 201
    And the "OrderPlaced" event remains unpublished in the outbox
    And the event is published once the broker recovers

  Scenario: Replaying an idempotency key returns the original order
    Given the customer placed an order with idempotency key "idem-99"
    When the same request is retried with idempotency key "idem-99"
    Then the response status is 201
    And the same orderId is returned
    And no second "OrderPlaced" event is enqueued

  Scenario: A conflicting body on a used idempotency key is rejected
    Given the customer placed an order with idempotency key "idem-99"
    When a different order body is sent with idempotency key "idem-99"
    Then the response status is 409

  Scenario Outline: Orders must have at least one valid line
    When the customer places an order with <lines>
    Then the response status is 400

    Examples:
      | lines              |
      | no lines           |
      | a quantity of 0    |
      | a negative price   |
