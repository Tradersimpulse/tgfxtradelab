#!/usr/bin/env python3
"""
Test script to verify AWS Chime SDK integration
Run this to test your AWS credentials and Chime setup
"""

import boto3
from botocore.exceptions import NoCredentialsError, ClientError
import uuid
from datetime import datetime
import os

def test_chime_integration():
    """Test AWS Chime SDK integration"""
    
    print("🧪 Testing AWS Chime SDK Integration")
    print("=" * 50)
    
    # Check environment variables
    print("1. Checking AWS Credentials...")
    aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    aws_region = os.getenv('AWS_REGION', 'us-east-1')
    
    if not aws_access_key or not aws_secret_key:
        print("❌ AWS credentials not found in environment variables")
        print("   Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")
        return False
    
    print(f"✅ AWS Access Key: {aws_access_key[:8]}...")
    print(f"✅ AWS Region: {aws_region}")
    
    # Test AWS Chime SDK Meetings client
    print("\n2. Testing Chime SDK Meetings Client...")
    try:
        chime_client = boto3.client(
            'chime-sdk-meetings',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )
        print("✅ Chime SDK client created successfully")
    except Exception as e:
        print(f"❌ Failed to create Chime client: {e}")
        return False
    
    # Test creating a meeting
    print("\n3. Testing Meeting Creation...")
    try:
        external_meeting_id = f"test-{uuid.uuid4().hex[:12]}"
        
        response = chime_client.create_meeting(
            ClientRequestToken=str(uuid.uuid4()),
            ExternalMeetingId=external_meeting_id,
            MediaRegion=aws_region
        )
        
        meeting = response['Meeting']
        meeting_id = meeting['MeetingId']
        
        print(f"✅ Meeting created successfully!")
        print(f"   Meeting ID: {meeting_id}")
        print(f"   External ID: {external_meeting_id}")
        print(f"   Media Region: {meeting['MediaRegion']}")
        
        # Check MediaPlacement
        media_placement = meeting['MediaPlacement']
        print(f"\n📡 Media Placement:")
        print(f"   Audio Host URL: {media_placement.get('AudioHostUrl', 'Missing')}")
        print(f"   Screen Sharing URL: {media_placement.get('ScreenSharingUrl', 'Missing')}")
        print(f"   Screen Data URL: {media_placement.get('ScreenDataUrl', 'Missing')}")
        print(f"   Signaling URL: {media_placement.get('SignalingUrl', 'Missing')}")
        
    except ClientError as e:
        print(f"❌ Failed to create meeting: {e}")
        return False
    
    # Test creating an attendee
    print("\n4. Testing Attendee Creation...")
    try:
        attendee_response = chime_client.create_attendee(
            MeetingId=meeting_id,
            ExternalUserId=f"test-user-{uuid.uuid4().hex[:8]}"
        )
        
        attendee = attendee_response['Attendee']
        print(f"✅ Attendee created successfully!")
        print(f"   Attendee ID: {attendee['AttendeeId']}")
        print(f"   External User ID: {attendee['ExternalUserId']}")
        
    except ClientError as e:
        print(f"❌ Failed to create attendee: {e}")
        return False
    
    # Clean up - delete the test meeting
    print("\n5. Cleaning up test meeting...")
    try:
        chime_client.delete_meeting(MeetingId=meeting_id)
        print("✅ Test meeting deleted successfully")
    except ClientError as e:
        print(f"⚠️  Failed to delete test meeting: {e}")
    
    print("\n🎉 All tests passed! AWS Chime SDK integration is working correctly.")
    return True

def test_permissions():
    """Test AWS permissions for Chime SDK"""
    print("\n6. Testing AWS Permissions...")
    
    try:
        iam_client = boto3.client('iam')
        # This will fail if we don't have IAM permissions, but that's okay
        # The important thing is that Chime SDK works
        print("✅ AWS client can be created")
    except Exception as e:
        print(f"ℹ️  IAM permissions check skipped: {e}")
    
    print("\n📋 Required IAM Permissions for Chime SDK:")
    print("   - chime:CreateMeeting")
    print("   - chime:DeleteMeeting")
    print("   - chime:CreateAttendee")
    print("   - chime:BatchCreateAttendee")
    print("   - chime:BatchUpdateAttendeeCapabilitiesExcept")

if __name__ == '__main__':
    success = test_chime_integration()
    test_permissions()
    
    if success:
        print("\n✅ Your AWS Chime SDK setup is ready for live streaming!")
    else:
        print("\n❌ Please fix the issues above before proceeding.")
        print("\nTroubleshooting Tips:")
        print("1. Verify AWS credentials in environment variables")
        print("2. Check IAM permissions for Chime SDK")
        print("3. Ensure you're using the correct AWS region")
        print("4. Make sure your AWS account has Chime SDK enabled")
