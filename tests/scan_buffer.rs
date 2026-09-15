use std::time::Duration;

use scrap_monitoring_lidar_simulator::scan_runtime::{LatestTwo, RingError};

#[tokio::test]
async fn late_subscriber_receives_retained_frames_and_exact_loss() {
    let ring = LatestTwo::new();
    ring.publish(1_u64).await.unwrap();
    ring.publish(2).await.unwrap();
    ring.publish(3).await.unwrap();

    let first = ring.next_after(0).await.unwrap();
    assert_eq!((first.serial, *first.item, first.lost), (2, 2, 1));
    let second = ring.next_after(first.serial).await.unwrap();
    assert_eq!((second.serial, *second.item, second.lost), (3, 3, 0));
}

#[tokio::test]
async fn connected_subscriber_waits_and_detects_overwrite() {
    let ring = LatestTwo::new();
    ring.publish(1_u64).await.unwrap();
    let first = ring.next_after(0).await.unwrap();
    let waiting_ring = ring.clone();
    let waiting = tokio::spawn(async move { waiting_ring.next_after(first.serial).await });
    tokio::task::yield_now().await;
    assert!(!waiting.is_finished());

    ring.publish(2).await.unwrap();
    let second = waiting.await.unwrap().unwrap();
    assert_eq!((second.serial, *second.item, second.lost), (2, 2, 0));
    ring.publish(3).await.unwrap();
    ring.publish(4).await.unwrap();
    ring.publish(5).await.unwrap();
    let overwritten = ring.next_after(second.serial).await.unwrap();
    assert_eq!(
        (overwritten.serial, *overwritten.item, overwritten.lost),
        (4, 4, 1)
    );
}

#[tokio::test]
async fn reconnect_replays_retained_frames_from_cursor_zero() {
    let ring = LatestTwo::new();
    ring.publish("one").await.unwrap();
    ring.publish("two").await.unwrap();
    let connected = ring.next_after(0).await.unwrap();
    assert_eq!(*connected.item, "one");

    let reconnected = ring.next_after(0).await.unwrap();
    assert_eq!((reconnected.serial, *reconnected.item), (1, "one"));
}

#[tokio::test]
async fn close_drains_retained_frames_then_returns_eof() {
    let ring = LatestTwo::new();
    ring.publish(10_u64).await.unwrap();
    ring.publish(20).await.unwrap();
    ring.close().await;

    let first = ring.next_after(0).await.unwrap();
    let second = ring.next_after(first.serial).await.unwrap();
    assert_eq!((*first.item, *second.item), (10, 20));
    assert!(ring.next_after(second.serial).await.is_none());
    assert_eq!(ring.publish(30).await.unwrap_err(), RingError::Closed);
}

#[tokio::test]
async fn close_wakes_an_empty_waiter() {
    let ring = LatestTwo::<u64>::new();
    let waiting_ring = ring.clone();
    let waiting = tokio::spawn(async move { waiting_ring.next_after(0).await });
    tokio::task::yield_now().await;
    ring.close().await;
    assert!(
        tokio::time::timeout(Duration::from_secs(1), waiting)
            .await
            .unwrap()
            .unwrap()
            .is_none()
    );
}

#[tokio::test]
async fn publish_wait_race_has_no_missed_wakeup() {
    for value in 1_u64..=256 {
        let ring = LatestTwo::new();
        let waiting_ring = ring.clone();
        let waiting = tokio::spawn(async move { waiting_ring.next_after(0).await });
        ring.publish(value).await.unwrap();
        let delivery = tokio::time::timeout(Duration::from_secs(1), waiting)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        assert_eq!(*delivery.item, value);
    }
}
