Feature: Greeting
  The starter contract. Replace it with real behaviour. Every schema
  element must be named in a scenario, and the linter rejects references
  to events this service neither owns nor consumes.

  Scenario: A greeting is published as a CloudEvents envelope
    When the name "Ada" is greeted
    Then a "GreeterGreeted" event is published on "greeter.greeted.v1"
    And the envelope carries specversion "1.0", a unique id, a subject and a time
    And the envelope source is /greeter with datacontenttype "application/json"
    And its type is __ORG__.greeter.greeted.v1
    And the data carries the greeting_id, name and greeted_at
    And handlers dedupe on the envelope id
