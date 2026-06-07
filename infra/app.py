#!/usr/bin/env python3
import os

import aws_cdk as cdk
from infra.infra_stack import InfraStack
from geocoder_stack import GeocoderStack

app = cdk.App()

GeocoderStack(
    app, "GeocoderStack",
    gnaf_url="https://data.gov.au/data/dataset/19432f89-dc3a-4ef3-b943-5326ef1dbecc/resource/f8666213-4079-44da-bede-ebda3a4363e0/download/g-naf_may26_allstates_gda2020_psv_1023.zip",
    gnaf_month_release="MAY 2026",
    env=cdk.Environment(
        account=os.environ['AWS_ACCOUNT_ID'],
        region="ap-southeast-2"
    )
)

app.synth()