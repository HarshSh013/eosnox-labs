"""Analyzer connections: receive results from the Unics H33 and 100plusV.

h33_hl7    parse H33 HL7 messages (copied from Test Connections/UnicsH33)
unics100   read the 100plusV result workbook over SSH
store      save received results, never editing them, and link them to registrations
network    network adapters and the H33 server address
workers    background threads that receive and poll
"""
