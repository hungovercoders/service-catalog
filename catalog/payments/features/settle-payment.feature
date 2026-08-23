Feature: Settling a payment
  Settlement is terminal. Authorisation is not.

  Scenario: Settlement emits exactly one event
    Given an authorised payment "p-1" for order "o-1" of 2500 pence
    When the processor confirms settlement
    Then a "PaymentSettled" event is published on "payments.settled.v1"
    And the event amountPence is 2500

  Scenario: Duplicate settlement callbacks are idempotent
    Given payment "p-1" has already settled
    When the processor sends the settlement callback again
    Then no second "PaymentSettled" event is published

  Scenario: A reversal after authorisation does not emit settlement
    Given an authorised payment "p-2"
    When the authorisation is reversed
    Then no "PaymentSettled" event is published for "p-2"
