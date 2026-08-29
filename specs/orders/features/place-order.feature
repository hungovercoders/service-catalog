Feature: Placing an order
  As a customer
  I want to place an order
  So that it is fulfilled once payment settles

  These scenarios are acceptance criteria, not suggestions. An
  implementation that fails any of them is wrong. If a scenario is
  itself wrong, that is a versioned change to this file.

  Background:
    Given the customer "c-1001" exists

  Scenario: A valid order is accepted and its event is guaranteed
    When the customer places an order for 2 units of "SKU-RED" at 1250 pence
    Then the response status is 201
    And the order status is "placed"
    And the order total is 2500 pence
    And an "OrderPlaced" event is guaranteed to be published for the order

  Scenario: The order and its event succeed or fail together
    Given event publication is unavailable
    When the customer places an order for 1 unit of "SKU-RED" at 1250 pence
    Then the response status is 201
    And the "OrderPlaced" event is published once publication recovers
    And no order ever exists without its "OrderPlaced" event

  Scenario: Replaying an idempotency key returns the original order
    Given the customer placed an order with idempotency key "idem-99"
    When the same request is retried with idempotency key "idem-99"
    Then the response status is 201
    And the same order_id is returned
    And no second "OrderPlaced" event is published

  Scenario: A conflicting body on a used idempotency key is rejected
    Given the customer placed an order with idempotency key "idem-99"
    When a different order body is sent with idempotency key "idem-99"
    Then the response status is 409

  Scenario: Order events are CloudEvents envelopes
    When the customer places an order for 2 units of "SKU-RED" at 1250 pence
    Then an "OrderPlaced" event is published on "orders.placed.v2"
    And the envelope carries specversion "1.0", a unique id and a time
    And the envelope source is /orders and its type is com.hungovercoders.orders.placed.v2
    And the envelope subject is the order_id, with datacontenttype "application/json"
    And the data carries the order_id, customer_id, placed_at and total_pence
    And handlers dedupe on the envelope id, not the natural key

  Scenario: Cancellation is a CloudEvents envelope too
    Given the customer placed an order
    When the order is cancelled because "out_of_stock"
    Then an "OrderCancelled" event is published on "orders.cancelled.v2"
    And its data carries the order_id, cancelled_at and reason

  Scenario: A settled payment marks the order paid
    Given the customer placed an order
    When a "PaymentSettled" event arrives on "payments.settled.v2" for that order_id
    Then the order status is "paid"
    And replaying the same envelope id does not change the order again

  Scenario: An order can be fetched by its id
    Given the customer placed an order via placeOrder
    When the order is fetched by order_id via getOrder
    Then the response status is 200
    And the order carries the order_id, customer_id, status and total_pence

  Scenario: Fetching an unknown order returns 404
    When an unknown order_id is fetched via getOrder
    Then the response status is 404

  Scenario Outline: Orders must have at least one valid line
    When the customer places an order with <lines>
    Then the response status is 400

    Examples:
      | lines                       |
      | no lines                    |
      | a line quantity of 0        |
      | a negative unit_price_pence |
      | a line missing its sku      |
