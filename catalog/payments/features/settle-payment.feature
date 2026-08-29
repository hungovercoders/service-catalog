Feature: Settling a payment
  Settlement is terminal. Authorisation is not.

  Scenario: Settlement emits exactly one event
    Given an authorised payment "p-1" for order "o-1" of 2500 pence
    When the processor confirms settlement
    Then a "PaymentSettled" event is published on "payments.settled.v2"
    And the event data amount_pence is 2500

  Scenario: Settlement events are CloudEvents envelopes
    Given an authorised payment "p-3" for order "o-3" of 1000 pence
    When the processor confirms settlement
    Then a "PaymentSettled" event is published on "payments.settled.v2"
    And the envelope carries specversion "1.0", a unique id and a time
    And the envelope source is /payments and its type is com.hungovercoders.payments.settled.v2
    And the envelope subject is the payment_id, with datacontenttype "application/json"
    And the data carries the payment_id, order_id, settled_at and amount_pence
    And handlers dedupe on the envelope id

  Scenario: Duplicate settlement callbacks are idempotent
    Given payment "p-1" has already settled
    When the processor sends the settlement callback again
    Then no second "PaymentSettled" event is published

  Scenario: A reversal after authorisation does not emit settlement
    Given an authorised payment "p-2"
    When the authorisation is reversed
    Then no "PaymentSettled" event is published for "p-2"
