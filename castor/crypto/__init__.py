"""PQC robot identity module (issue #808)."""

from castor.crypto.pqc import (
    RobotKeyPair,
    generate_robot_keypair,
    robot_identity_record,
    sign_robot_message,
    verify_robot_message,
)

__all__ = [
    "RobotKeyPair",
    "generate_robot_keypair",
    "robot_identity_record",
    "sign_robot_message",
    "verify_robot_message",
]
