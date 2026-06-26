#!/usr/bin/env python3
import aws_cdk as cdk
from geocoder_stack import GeocoderStack

app = cdk.App()

GeocoderStack(
    app, "GeocoderStack",
    env=cdk.Environment(
        region="ap-southeast-2"
    )
)

app.synth()